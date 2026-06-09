"""Image overlay management for video composition."""
import logging
import os
from typing import List, Dict, Optional

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

log = logging.getLogger(__name__)


class OverlayManager:
    """Manages static image overlays on the compositor via imagefreeze."""

    def __init__(self, overlays_config: List[Dict]):
        self.overlays_config = overlays_config
        Gst.init(None)

    def add_overlays_to_pipeline(
        self,
        pipeline: Gst.Pipeline,
        compositor: Gst.Element,
        compositor_index: int,
        fps: int,
    ) -> int:
        """Add imagefreeze-based overlay branches to the compositor.

        Each overlay is: filesrc → decoder → imagefreeze → videoconvert → videoscale
                         → capsfilter(size+fps) → compositor sink pad

        Returns the next available compositor_index.
        """
        for i, ovl_cfg in enumerate(self.overlays_config):
            image_path = ovl_cfg.get("image", "")
            if not image_path or not os.path.exists(image_path):
                log.warning("Overlay %d: image not found: %s", i, image_path)
                continue

            ovl = f"ovl-{compositor_index}"
            ext = os.path.splitext(image_path)[1].lower()

            filesrc = Gst.ElementFactory.make("filesrc", f"{ovl}-filesrc")
            if not filesrc:
                log.error("Overlay %d: could not create filesrc", i)
                continue
            filesrc.set_property("location", image_path)

            decoder = Gst.ElementFactory.make(
                "jpegdec" if ext in (".jpg", ".jpeg") else "pngdec",
                f"{ovl}-decoder",
            )
            if not decoder:
                log.error("Overlay %d: no decoder for %s", i, image_path)
                continue

            freeze = Gst.ElementFactory.make("imagefreeze", f"{ovl}-freeze")
            if not freeze:
                log.error(
                    "Overlay %d: imagefreeze unavailable — "
                    "install gstreamer1.0-plugins-good", i,
                )
                continue
            try:
                freeze.set_property("is-live", True)
            except (TypeError, AttributeError):
                pass

            vconv = Gst.ElementFactory.make("videoconvert", f"{ovl}-convert")
            vscale = Gst.ElementFactory.make("videoscale", f"{ovl}-scale")

            ovl_size = ovl_cfg.get("size", {})
            ovl_w = ovl_size.get("width", 200)
            ovl_h = ovl_size.get("height", 200)

            size_caps = Gst.ElementFactory.make("capsfilter", f"{ovl}-caps")
            size_caps.set_property(
                "caps",
                Gst.Caps.from_string(
                    f"video/x-raw,width={ovl_w},height={ovl_h},framerate={fps}/1"
                ),
            )

            for el in (filesrc, decoder, freeze, vconv, vscale, size_caps):
                pipeline.add(el)

            filesrc.link(decoder)
            decoder.link(freeze)
            freeze.link(vconv)
            vconv.link(vscale)
            vscale.link(size_caps)

            sink_pad = compositor.request_pad_simple(f"sink_{compositor_index}")
            if sink_pad is None:
                log.error("Failed to request compositor pad for overlay %d", i)
                continue
            ovl_pos = ovl_cfg.get("position", {"x": 0, "y": 0})
            sink_pad.set_property("xpos", ovl_pos.get("x", 0))
            sink_pad.set_property("ypos", ovl_pos.get("y", 0))
            sink_pad.set_property("width", ovl_w)
            sink_pad.set_property("height", ovl_h)
            sink_pad.set_property("zorder", ovl_cfg.get("z_order", 100 + i))
            sink_pad.set_property("alpha", float(ovl_cfg.get("opacity", 1.0)))

            size_caps.get_static_pad("src").link(sink_pad)
            compositor_index += 1

            log.info(
                "Overlay %d: %s at (%d,%d) %dx%d opacity=%.2f",
                i, os.path.basename(image_path),
                ovl_pos.get("x", 0), ovl_pos.get("y", 0),
                ovl_w, ovl_h, ovl_cfg.get("opacity", 1.0),
            )

        return compositor_index

    def get_overlay_config(self) -> List[Dict]:
        return self.overlays_config
