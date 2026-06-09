"""Shared GStreamer helpers for V4L2 capture (HDMI + USB webcams)."""
import logging
from typing import List, Optional, Tuple

import gi

log = logging.getLogger(__name__)
gi.require_version('Gst', '1.0')
from gi.repository import Gst


ROCKCHIP_RAW_FORMATS = [
    "NV12", "NM12", "YU12", "NV21", "NV16", "YV12",
    "BGR3", "RGB3", "AR24", "BA24", "XR24", "RGBP",
]


def _add_elements(pipeline: Gst.Pipeline, *elements: Gst.Element):
    for el in elements:
        pipeline.add(el)


def _link_chain(*elements: Gst.Element) -> bool:
    for a, b in zip(elements, elements[1:]):
        if not a.link(b):
            return False
    return True


def _cleanup(pipeline: Gst.Pipeline, elements: List[Gst.Element]):
    for el in elements:
        try:
            el.set_state(Gst.State.NULL)
            pipeline.remove(el)
        except Exception:
            pass


_FLIP_METHOD = {90: 1, 180: 2, 270: 3}


def _insert_rotation(
    pipeline: Gst.Pipeline,
    prefix: str,
    prev: Gst.Element,
    elements: List[Gst.Element],
    rotation: int,
) -> Gst.Element:
    """Insert videoflip right after decode, before any scaling. Mutates elements."""
    method = _FLIP_METHOD.get(rotation % 360, 0) if rotation else 0
    if not method:
        return prev
    flip = Gst.ElementFactory.make("videoflip", f"{prefix}-flip")
    if not flip:
        log.warning("videoflip unavailable — rotation skipped for %s", prefix)
        return prev
    flip.set_property("method", method)
    pipeline.add(flip)
    prev.link(flip)
    elements.append(flip)
    return flip


def _make_v4l2src(name: str, device_path: str, source_type: str = "") -> Gst.Element:
    source = Gst.ElementFactory.make("v4l2src", name)
    source.set_property("device", device_path)
    try:
        source.set_property("do-timestamp", True)
    except (TypeError, ValueError):
        pass
    io_modes = (4, 2, 1, 0) if source_type == "hdmi" else (2, 0, 1, 4)
    for io_mode in io_modes:
        try:
            source.set_property("io-mode", io_mode)
            break
        except (TypeError, ValueError):
            continue
    return source


def _make_output_chain(
    pipeline: Gst.Pipeline,
    prefix: str,
    target_width: int,
    target_height: int,
    fps: int,
    require_fps: bool = True,
) -> Tuple[Gst.Element, Gst.Element, Gst.Element, Gst.Element]:
    convert = Gst.ElementFactory.make("videoconvert", f"{prefix}-convert")
    scale = Gst.ElementFactory.make("videoscale", f"{prefix}-scale")
    out_filter = Gst.ElementFactory.make("capsfilter", f"{prefix}-out-caps")
    if require_fps:
        caps_str = (
            f"video/x-raw,width={target_width},height={target_height},"
            f"framerate={fps}/1"
        )
    else:
        caps_str = f"video/x-raw,width={target_width},height={target_height}"
    out_filter.set_property("caps", Gst.Caps.from_string(caps_str))
    _add_elements(pipeline, convert, scale, out_filter)
    return convert, scale, out_filter, out_filter


def _resolve_format_order(
    formats: Optional[List[str]],
    capabilities: Optional[List[str]],
    source_type: str,
    preferred_format: Optional[str] = None,
) -> List[str]:
    hdmi_default = ROCKCHIP_RAW_FORMATS + ["H264", "MJPG", "YUYV"]
    usb_default = ["MJPG", "YUYV", "NV12", "H264"]
    base = hdmi_default if source_type == "hdmi" else usb_default

    probed: List[str] = []
    if preferred_format:
        token = preferred_format.upper().replace("MJPEG", "MJPG")
        probed.append(token)
    for src in (formats or []):
        token = src.upper().replace("MJPEG", "MJPG")
        if token not in probed:
            probed.append(token)

    order: List[str] = []
    if source_type == "hdmi":
        for token in ("NV12", "NM12"):
            if token not in order:
                order.append(token)
    for token in probed + base:
        if token not in order:
            order.append(token)
    return order


def _raw_caps_attempts(
    pixel_format: Optional[str],
    fps: int,
    input_width: Optional[int],
    input_height: Optional[int],
) -> List[str]:
    attempts: List[str] = []
    fmt = pixel_format if pixel_format and pixel_format not in ("H264", "MJPG") else None

    if fmt and input_width and input_height:
        attempts.append(
            f"video/x-raw,format={fmt},width={input_width},height={input_height}"
        )
    if fmt:
        attempts.append(f"video/x-raw,format={fmt}")
    if input_width and input_height:
        attempts.append(
            f"video/x-raw,format=NV12,width={input_width},height={input_height}"
        )
        attempts.append(f"video/x-raw,width={input_width},height={input_height}")
    attempts.append(f"video/x-raw,framerate={fps}/1")
    attempts.append("video/x-raw")
    return attempts


def _try_h264(
    pipeline: Gst.Pipeline,
    prefix: str,
    device_path: str,
    target_width: int,
    target_height: int,
    fps: int,
    source_type: str = "",
    rotation: int = 0,
) -> Optional[Gst.Element]:
    for attempt, caps_str in enumerate((
        f"video/x-h264,framerate={fps}/1",
        "video/x-h264",
    )):
        source = _make_v4l2src(f"{prefix}-h264-src-{attempt}", device_path, source_type)
        in_caps = Gst.ElementFactory.make("capsfilter", f"{prefix}-h264-in-caps-{attempt}")
        in_caps.set_property("caps", Gst.Caps.from_string(caps_str))
        parser = Gst.ElementFactory.make("h264parse", f"{prefix}-h264parse-{attempt}")
        decoder = None
        for factory in ("v4l2h264dec", "v4l2slh264dec", "avdec_h264", "openh264dec"):
            decoder = Gst.ElementFactory.make(factory, f"{prefix}-h264dec-{attempt}")
            if decoder:
                break
        if not decoder:
            continue

        elements = [source, in_caps, parser, decoder]
        _add_elements(pipeline, source, in_caps, parser, decoder)
        if not _link_chain(source, in_caps, parser, decoder):
            _cleanup(pipeline, elements)
            continue

        prev = _insert_rotation(pipeline, f"{prefix}-h264-{attempt}", decoder, elements, rotation)
        convert, scale, out_filter, tail = _make_output_chain(
            pipeline, f"{prefix}-h264-{attempt}", target_width, target_height, fps,
        )
        elements += [convert, scale, out_filter]
        if _link_chain(prev, convert, scale, out_filter):
            return tail
        _cleanup(pipeline, elements)
    return None


def _try_mjpeg(
    pipeline: Gst.Pipeline,
    prefix: str,
    device_path: str,
    target_width: int,
    target_height: int,
    fps: int,
    source_type: str = "",
    rotation: int = 0,
) -> Optional[Gst.Element]:
    for attempt, caps_str in enumerate((
        f"image/jpeg,framerate={fps}/1",
        "image/jpeg",
    )):
        source = _make_v4l2src(f"{prefix}-mjpeg-src-{attempt}", device_path, source_type)
        in_caps = Gst.ElementFactory.make("capsfilter", f"{prefix}-mjpeg-in-caps-{attempt}")
        in_caps.set_property("caps", Gst.Caps.from_string(caps_str))
        jpegdec = Gst.ElementFactory.make("jpegdec", f"{prefix}-jpegdec-{attempt}")

        elements = [source, in_caps, jpegdec]
        _add_elements(pipeline, source, in_caps, jpegdec)
        if not _link_chain(source, in_caps, jpegdec):
            _cleanup(pipeline, elements)
            continue

        prev = _insert_rotation(pipeline, f"{prefix}-mjpeg-{attempt}", jpegdec, elements, rotation)
        convert, scale, out_filter, tail = _make_output_chain(
            pipeline, f"{prefix}-mjpeg-{attempt}", target_width, target_height, fps,
        )
        elements += [convert, scale, out_filter]
        if _link_chain(prev, convert, scale, out_filter):
            return tail
        _cleanup(pipeline, elements)
    return None


def _try_raw(
    pipeline: Gst.Pipeline,
    prefix: str,
    device_path: str,
    target_width: int,
    target_height: int,
    fps: int,
    pixel_format: Optional[str] = None,
    input_width: Optional[int] = None,
    input_height: Optional[int] = None,
    require_fps: bool = True,
    source_type: str = "",
    rotation: int = 0,
) -> Optional[Gst.Element]:
    caps_attempts = _raw_caps_attempts(pixel_format, fps, input_width, input_height)

    for attempt, caps_str in enumerate(caps_attempts):
        source = _make_v4l2src(f"{prefix}-raw-src-{attempt}", device_path, source_type)
        in_caps = Gst.ElementFactory.make("capsfilter", f"{prefix}-raw-in-caps-{attempt}")
        in_caps.set_property("caps", Gst.Caps.from_string(caps_str))

        elements = [source, in_caps]
        _add_elements(pipeline, source, in_caps)
        if not _link_chain(source, in_caps):
            _cleanup(pipeline, elements)
            continue

        prev = _insert_rotation(pipeline, f"{prefix}-raw-{attempt}", in_caps, elements, rotation)
        convert, scale, out_filter, tail = _make_output_chain(
            pipeline, f"{prefix}-raw-{attempt}", target_width, target_height, fps,
            require_fps=require_fps,
        )
        elements += [convert, scale, out_filter]
        if _link_chain(prev, convert, scale, out_filter):
            return tail
        _cleanup(pipeline, elements)

    return None


def configure_compositor(compositor: Gst.Element):
    """Prefer output as soon as the first live source produces frames."""
    try:
        compositor.set_property("start-time-selection", 1)
    except (TypeError, ValueError):
        pass


def add_v4l2_source_branch(
    pipeline: Gst.Pipeline,
    prefix: str,
    device_path: str,
    target_width: int,
    target_height: int,
    fps: int = 30,
    capabilities: Optional[List[str]] = None,
    source_type: str = "",
    formats: Optional[List[str]] = None,
    input_width: Optional[int] = None,
    input_height: Optional[int] = None,
    input_format: Optional[str] = None,
    rotation: int = 0,
) -> Tuple[Optional[Gst.Element], str]:
    """Add a V4L2 capture branch; returns (tail_element, format_used).

    Rotation (0/90/180/270) is applied immediately after decode, before scaling,
    so the compositor always receives frames at target_width × target_height.
    """
    is_hdmi = source_type == "hdmi"
    require_fps = not is_hdmi
    order = _resolve_format_order(formats, capabilities, source_type, input_format)

    for fmt in order:
        tail = None
        if fmt == "H264":
            tail = _try_h264(
                pipeline, prefix, device_path, target_width, target_height, fps,
                source_type=source_type, rotation=rotation,
            )
        elif fmt == "MJPG":
            tail = _try_mjpeg(
                pipeline, prefix, device_path, target_width, target_height, fps,
                source_type=source_type, rotation=rotation,
            )
        else:
            tail = _try_raw(
                pipeline, prefix, device_path, target_width, target_height, fps, fmt,
                input_width=input_width if is_hdmi else None,
                input_height=input_height if is_hdmi else None,
                require_fps=require_fps,
                source_type=source_type,
                rotation=rotation,
            )
        if tail:
            log.info("%s using %s from %s (rotation=%d)", prefix, fmt, device_path, rotation)
            return tail, fmt.lower()

    log.warning("All formats failed for %s (tried %s)", device_path, order)
    return None, "none"
