"""Per-source video transforms: rotation (videoflip) and circle masking (cairooverlay)."""
import logging
import math

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

log = logging.getLogger(__name__)

try:
    import cairo as _cairo
    _CAIRO_OK = True
except ImportError:
    _CAIRO_OK = False
    log.warning("pycairo not available — circle mask disabled")

_FLIP_METHOD = {90: 1, 180: 2, 270: 3}


def add_rotation(
    pipeline: Gst.Pipeline,
    prefix: str,
    prev: Gst.Element,
    rotation: int,
) -> Gst.Element:
    """Insert videoflip for 90/180/270° rotation. Returns prev unchanged if rotation is 0."""
    method = _FLIP_METHOD.get(rotation % 360, 0)
    if method == 0:
        return prev
    flip = Gst.ElementFactory.make("videoflip", f"{prefix}-flip")
    if not flip:
        log.warning("videoflip unavailable (install gstreamer1.0-plugins-good)")
        return prev
    flip.set_property("method", method)
    pipeline.add(flip)
    prev.link(flip)
    return flip


def add_polygon_mask(
    pipeline: Gst.Pipeline,
    prefix: str,
    prev: Gst.Element,
    points: list,
) -> Gst.Element:
    """Mask the source to an arbitrary polygon defined by normalised 0–1 points.

    Uses the same DEST_OUT + EVEN_ODD technique as the circle mask: the path is
    (full_rect ∖ polygon), so everything outside the polygon is zeroed out.
    ``points`` is a list of [x, y] pairs in 0–1 source-relative coordinates.
    """
    if not _CAIRO_OK:
        log.warning("pycairo unavailable — polygon mask skipped for %s", prefix)
        return prev
    if len(points) < 3:
        return prev

    to_bgra = Gst.ElementFactory.make("videoconvert", f"{prefix}-pmask-conv")
    bgra_caps = Gst.ElementFactory.make("capsfilter", f"{prefix}-pmask-caps")
    bgra_caps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=BGRA"))
    overlay = Gst.ElementFactory.make("cairooverlay", f"{prefix}-pmask-ovl")
    out_conv = Gst.ElementFactory.make("videoconvert", f"{prefix}-pmask-out")

    if not overlay:
        log.warning("cairooverlay unavailable (install gstreamer1.0-plugins-bad)")
        return prev

    for el in (to_bgra, bgra_caps, overlay, out_conv):
        pipeline.add(el)
    prev.link(to_bgra)
    to_bgra.link(bgra_caps)
    bgra_caps.link(overlay)
    overlay.link(out_conv)

    pts = [list(p) for p in points]  # snapshot

    def _draw(ovl, cr, ts, dur):
        try:
            target = cr.get_target()
            w, h = target.get_width(), target.get_height()
        except Exception:
            return
        if w == 0 or h == 0:
            return
        cr.set_operator(_cairo.OPERATOR_DEST_OUT)
        cr.set_fill_rule(_cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, w, h)
        cr.move_to(pts[0][0] * w, pts[0][1] * h)
        for px, py in pts[1:]:
            cr.line_to(px * w, py * h)
        cr.close_path()
        cr.set_source_rgba(0, 0, 0, 1)
        cr.fill()

    overlay.connect("draw", _draw)
    return out_conv


def _make_cairo_mask_elements(pipeline: Gst.Pipeline, prefix: str, prev: Gst.Element, tag: str):
    """Shared setup for cairooverlay-based masks. Returns (overlay, out_conv) or (None, None)."""
    to_bgra  = Gst.ElementFactory.make("videoconvert", f"{prefix}-{tag}-conv")
    bgra_caps = Gst.ElementFactory.make("capsfilter",   f"{prefix}-{tag}-caps")
    bgra_caps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=BGRA"))
    overlay  = Gst.ElementFactory.make("cairooverlay",  f"{prefix}-{tag}-ovl")
    out_conv = Gst.ElementFactory.make("videoconvert",  f"{prefix}-{tag}-out")
    if not overlay:
        log.warning("cairooverlay unavailable (install gstreamer1.0-plugins-bad)")
        return None, None
    for el in (to_bgra, bgra_caps, overlay, out_conv):
        pipeline.add(el)
    prev.link(to_bgra)
    to_bgra.link(bgra_caps)
    bgra_caps.link(overlay)
    overlay.link(out_conv)
    return overlay, out_conv


def add_circle_mask(
    pipeline: Gst.Pipeline,
    prefix: str,
    prev: Gst.Element,
    mask_x: float = None,
    mask_y: float = None,
    mask_w: float = None,
    mask_h: float = None,
) -> Gst.Element:
    """Clip source to a circle.

    If mask_x/y/w/h are provided (pixels in the source frame after scaling), the
    circle is inscribed in that bounding box. Otherwise defaults to a centred circle
    that fills the full source frame.

    Requires pycairo and gstreamer1.0-plugins-bad (cairooverlay).
    """
    if not _CAIRO_OK:
        log.warning("pycairo unavailable — circle mask skipped for %s", prefix)
        return prev

    overlay, out_conv = _make_cairo_mask_elements(pipeline, prefix, prev, "cmask")
    if overlay is None:
        return prev

    has_area = mask_w and mask_h

    def _draw(ovl, cr, ts, dur):
        try:
            target = cr.get_target()
            w, h = target.get_width(), target.get_height()
        except Exception:
            return
        if w == 0 or h == 0:
            return
        if has_area:
            cx = mask_x + mask_w / 2
            cy = mask_y + mask_h / 2
            r  = min(mask_w, mask_h) / 2
        else:
            cx, cy, r = w / 2, h / 2, min(w, h) / 2
        cr.set_operator(_cairo.OPERATOR_DEST_OUT)
        cr.set_fill_rule(_cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, w, h)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 1)
        cr.fill()

    overlay.connect("draw", _draw)
    return out_conv


def add_rect_mask(
    pipeline: Gst.Pipeline,
    prefix: str,
    prev: Gst.Element,
    mask_x: float,
    mask_y: float,
    mask_w: float,
    mask_h: float,
) -> Gst.Element:
    """Clip source to a rectangle at (mask_x, mask_y, mask_w, mask_h) in source pixels.

    Everything outside the rectangle is made transparent; the compositor sees through it.
    Requires pycairo and gstreamer1.0-plugins-bad.
    """
    if not _CAIRO_OK:
        log.warning("pycairo unavailable — rect mask skipped for %s", prefix)
        return prev

    overlay, out_conv = _make_cairo_mask_elements(pipeline, prefix, prev, "rmask")
    if overlay is None:
        return prev

    def _draw(ovl, cr, ts, dur):
        try:
            target = cr.get_target()
            w, h = target.get_width(), target.get_height()
        except Exception:
            return
        if w == 0 or h == 0:
            return
        cr.set_operator(_cairo.OPERATOR_DEST_OUT)
        cr.set_fill_rule(_cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, w, h)
        cr.rectangle(mask_x, mask_y, mask_w, mask_h)
        cr.set_source_rgba(0, 0, 0, 1)
        cr.fill()

    overlay.connect("draw", _draw)
    return out_conv
