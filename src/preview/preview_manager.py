"""Live preview pipeline for the Qt UI and web admin."""
import asyncio
import io
import logging
import os
import threading
import time
from typing import Callable, Dict, Any, Optional, Set

import gi

log = logging.getLogger(__name__)
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from src.composition.transform import add_circle_mask, add_polygon_mask, add_rect_mask, add_rotation
from src.recording.recorder import _apply_mask

try:
    from PIL import Image as _PILImage
    _PIL_OK = True
except ImportError:
    _PIL_OK = False


class PreviewManager:
    """One lightweight preview pipeline per source; composite uses HDMI when available."""

    def __init__(
        self,
        get_config: Callable[[], Dict[str, Any]],
        on_jpeg: Callable[[bytes], None],
    ):
        self._get_config = get_config
        self._on_jpeg = on_jpeg
        self._latest_jpeg: Optional[bytes] = None
        self._source_jpegs: Dict[str, bytes] = {}
        self._jpeg_lock = threading.Lock()
        self._pipelines: Dict[str, Gst.Pipeline] = {}
        self._is_running = False
        self._last_error: Optional[str] = None

        # Push-based MJPEG: asyncio queues per subscriber key.
        # Key is source_id or "__composite__".
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._subs_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # PIL-based composite (idle preview)
        self._last_composite_time: float = 0.0
        self._composite_interval: float = 1 / 30  # 30 fps

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Inject the web server's asyncio event loop for push-based MJPEG."""
        self._loop = loop

    def subscribe(self, key: str = "__composite__") -> asyncio.Queue:
        """Register a new MJPEG subscriber queue. Call from async context."""
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        with self._subs_lock:
            self._subscribers.setdefault(key, set()).add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue, key: str = "__composite__"):
        with self._subs_lock:
            self._subscribers.get(key, set()).discard(q)

    def _push_to_subscribers(self, key: str, jpeg: bytes):
        loop = self._loop
        if not loop:
            return
        with self._subs_lock:
            queues = list(self._subscribers.get(key, set()))
        for q in queues:
            def _put(q=q, jpeg=jpeg):
                # Drain stale frame then push new one (runs in event loop thread)
                while not q.empty():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                try:
                    q.put_nowait(jpeg)
                except asyncio.QueueFull:
                    pass
            try:
                loop.call_soon_threadsafe(_put)
            except RuntimeError:
                pass

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._jpeg_lock:
            return self._latest_jpeg

    def get_source_jpeg(self, source_id: str) -> Optional[bytes]:
        with self._jpeg_lock:
            return self._source_jpegs.get(source_id)

    def list_sources(self) -> list[dict]:
        config = self._get_config()
        sources = []
        for src in config.get("sources", []):
            sid = src.get("id") or src.get("device", "").replace("/dev/", "")
            if not sid:
                continue
            with self._jpeg_lock:
                has_frame = sid in self._source_jpegs
            sources.append({
                "id": sid,
                "type": src.get("type", "unknown"),
                "device": src.get("device", ""),
                "formats": src.get("formats", []),
                "has_frame": has_frame,
                "preview_running": sid in self._pipelines,
                "hdmi_signal": src.get("hdmi_signal"),
                "capture_size": (
                    f"{src['capture_width']}x{src['capture_height']}"
                    if src.get("capture_width") and src.get("capture_height")
                    else None
                ),
            })
        return sources

    def get_status(self) -> dict:
        with self._jpeg_lock:
            has_composite = self._latest_jpeg is not None
            source_count = len(self._source_jpegs)
        return {
            "running": self._is_running,
            "pipeline_count": len(self._pipelines),
            "has_composite": has_composite,
            "source_count": source_count,
            "error": self._last_error,
        }

    @staticmethod
    def _make_jpeg_enc(name: str) -> Gst.Element:
        for factory in ("jpegenc", "avenc_mjpeg", "v4l2jpegenc"):
            enc = Gst.ElementFactory.make(factory, name)
            if enc:
                if factory == "jpegenc":
                    enc.set_property("quality", 75)
                return enc
        raise RuntimeError("No JPEG encoder available (install gstreamer1.0-plugins-good)")

    def _primary_source_id(self) -> Optional[str]:
        sources = self._get_config().get("sources", [])
        for src in sources:
            if src.get("type") == "hdmi":
                return src.get("id")
        return sources[0].get("id") if sources else None

    def _compose_preview_frame(self) -> Optional[bytes]:
        """PIL-composite all per-source JPEGs into the layout output frame."""
        if not _PIL_OK:
            return None
        config = self._get_config()
        output = config.get("output", {})
        out_w = output.get("width", 1920)
        out_h = output.get("height", 1080)
        PREV_W, PREV_H = 640, 360
        scale = min(PREV_W / out_w, PREV_H / out_h)
        cw, ch = int(out_w * scale), int(out_h * scale)
        canvas = _PILImage.new("RGB", (cw, ch), (0, 0, 0))
        for src in sorted(config.get("sources", []), key=lambda s: s.get("z_order", 0)):
            sid = src.get("id")
            if not sid:
                continue
            with self._jpeg_lock:
                jpeg = self._source_jpegs.get(sid)
            if not jpeg:
                continue
            try:
                img = _PILImage.open(io.BytesIO(jpeg)).convert("RGB")
            except Exception:
                continue
            pos = src.get("position", {"x": 0, "y": 0})
            sz = src.get("size", {"width": out_w, "height": out_h})
            dx = int(pos.get("x", 0) * scale)
            dy = int(pos.get("y", 0) * scale)
            dw = max(1, int(sz.get("width", out_w) * scale))
            dh = max(1, int(sz.get("height", out_h) * scale))
            img = img.resize((dw, dh), _PILImage.BILINEAR)
            canvas.paste(img, (dx, dy))
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=70)
        return buf.getvalue()

    def _maybe_update_composite(self):
        """Rate-limited PIL composite update for the idle preview."""
        now = time.monotonic()
        if now - self._last_composite_time < self._composite_interval:
            return
        self._last_composite_time = now
        composite = self._compose_preview_frame()
        if composite:
            with self._jpeg_lock:
                self._latest_jpeg = composite
            self._on_jpeg(composite)
            self._push_to_subscribers("__composite__", composite)

    def _store_jpeg(self, data: bytes, source_id: str):
        with self._jpeg_lock:
            self._source_jpegs[source_id] = data
        self._push_to_subscribers(source_id, data)
        self._maybe_update_composite()

    def _make_sample_handler(self, source_id: str):
        def handler(appsink) -> Gst.FlowReturn:
            sample = appsink.emit("pull-sample")
            if not sample:
                return Gst.FlowReturn.OK
            buf = sample.get_buffer()
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if ok:
                try:
                    self._store_jpeg(bytes(mapinfo.data), source_id)
                finally:
                    buf.unmap(mapinfo)
            return Gst.FlowReturn.OK
        return handler

    def build_source_pipeline(self, source_config: dict) -> Gst.Pipeline:
        """Independent preview pipeline for a single V4L2 device.

        Uses format-agnostic caps on the source side so GStreamer can negotiate
        whatever the device natively produces.  The old approach of demanding a
        specific pixel format caused not-negotiated failures because pad linking
        is lazy — a link to capsfilter(NV12) succeeds even when the device only
        outputs BGR3, and the mismatch is only discovered when frames actually flow.
        """
        source_id = source_config.get("id", "source0")
        device_path = source_config.get("device")
        if not device_path:
            raise RuntimeError(f"No device path for {source_id}")

        formats = source_config.get("formats") or []
        fmt_upper = {f.upper().replace("MJPEG", "MJPG") for f in formats}
        source_type = source_config.get("type", "")
        source_fps: int | None = source_config.get("fps")
        preview_w, preview_h = 640, 360
        p = f"preview-{source_id}"

        pipeline = Gst.Pipeline.new(p)

        src = Gst.ElementFactory.make("v4l2src", f"{p}-src")
        if not src:
            raise RuntimeError("Could not create v4l2src")
        src.set_property("device", device_path)
        try:
            src.set_property("do-timestamp", True)
        except (TypeError, ValueError):
            pass
        io_modes = (4, 2, 1, 0) if source_type == "hdmi" else (2, 0, 1, 4)
        for io_mode in io_modes:
            try:
                src.set_property("io-mode", io_mode)
                break
            except (TypeError, ValueError):
                continue
        pipeline.add(src)
        prev = src

        fps_suffix = f",framerate={source_fps}/1" if source_fps else ""
        used_fmt = "raw"
        if "MJPG" in fmt_upper:
            in_caps = Gst.ElementFactory.make("capsfilter", f"{p}-in-caps")
            in_caps.set_property("caps", Gst.Caps.from_string(f"image/jpeg{fps_suffix}"))
            jpegdec = Gst.ElementFactory.make("jpegdec", f"{p}-jpegdec")
            if not jpegdec:
                raise RuntimeError("No JPEG decoder (install gstreamer1.0-plugins-good)")
            pipeline.add(in_caps)
            pipeline.add(jpegdec)
            prev.link(in_caps)
            in_caps.link(jpegdec)
            prev = jpegdec
            used_fmt = "mjpg"
        elif "H264" in fmt_upper:
            in_caps = Gst.ElementFactory.make("capsfilter", f"{p}-in-caps")
            in_caps.set_property("caps", Gst.Caps.from_string(f"video/x-h264{fps_suffix}"))
            parser = Gst.ElementFactory.make("h264parse", f"{p}-parser")
            decoder = None
            for factory in ("v4l2h264dec", "v4l2slh264dec", "avdec_h264", "openh264dec"):
                decoder = Gst.ElementFactory.make(factory, f"{p}-decoder")
                if decoder:
                    break
            if not decoder:
                raise RuntimeError("No H264 decoder available")
            pipeline.add(in_caps)
            pipeline.add(parser)
            pipeline.add(decoder)
            prev.link(in_caps)
            in_caps.link(parser)
            parser.link(decoder)
            prev = decoder
            used_fmt = "h264"
        elif source_fps:
            in_caps = Gst.ElementFactory.make("capsfilter", f"{p}-in-caps")
            in_caps.set_property("caps", Gst.Caps.from_string(f"video/x-raw{fps_suffix}"))
            pipeline.add(in_caps)
            prev.link(in_caps)
            prev = in_caps

        # rotation + shape mask (optional, from source config)
        rotation = source_config.get("rotation", 0)
        if rotation:
            prev = add_rotation(pipeline, p, prev, rotation)
        prev = _apply_mask(pipeline, p, prev, source_config)

        videoconvert = Gst.ElementFactory.make("videoconvert", f"{p}-convert")
        videoscale = Gst.ElementFactory.make("videoscale", f"{p}-scale")
        out_caps = Gst.ElementFactory.make("capsfilter", f"{p}-out")
        out_caps.set_property(
            "caps",
            Gst.Caps.from_string(f"video/x-raw,width={preview_w},height={preview_h}"),
        )
        jpegenc = self._make_jpeg_enc(f"{p}-jpeg")
        appsink = Gst.ElementFactory.make("appsink", f"{p}-sink")
        appsink.set_property("emit-signals", True)
        appsink.set_property("max-buffers", 1)
        appsink.set_property("drop", True)
        appsink.set_property("sync", False)
        appsink.connect("new-sample", self._make_sample_handler(source_id))

        for el in (videoconvert, videoscale, out_caps, jpegenc, appsink):
            pipeline.add(el)

        prev.link(videoconvert)
        videoconvert.link(videoscale)
        videoscale.link(out_caps)
        out_caps.link(jpegenc)
        jpegenc.link(appsink)

        log.info("Built pipeline for %s (%s, %s)", source_id, device_path, used_fmt)
        return pipeline

    def _on_bus_message(self, bus, message, source_id: str):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self._last_error = f"{source_id}: {err.message} ({debug})"
            log.error("Pipeline error: %s", self._last_error)

    def _start_pipeline(self, pipeline: Gst.Pipeline, source_id: str) -> bool:
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message, source_id)

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.error("%s set_state returned FAILURE", source_id)
            return False

        if ret == Gst.StateChangeReturn.ASYNC:
            ret, _, _ = pipeline.get_state(10 * Gst.SECOND)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error("%s async preroll failed", source_id)
                return False

        msg = bus.timed_pop_filtered(2 * Gst.SECOND, Gst.MessageType.ERROR)
        if msg:
            err, debug = msg.parse_error()
            self._last_error = f"{source_id}: {err.message} ({debug})"
            log.error("Pipeline error after start: %s", self._last_error)
            return False

        return True

    def _stop_pipeline(self, pipeline: Gst.Pipeline):
        bus = pipeline.get_bus()
        bus.remove_signal_watch()
        pipeline.set_state(Gst.State.NULL)

    def start_idle(self):
        if self._is_running:
            return

        self._last_error = None
        sources_config = [
            s for s in self._get_config().get("sources", [])
            if s.get("device") and os.path.exists(s["device"])
        ]
        if not sources_config:
            self._last_error = "No video capture devices available for preview"
            log.warning("%s", self._last_error)
            return

        started = 0
        for source_config in sources_config:
            source_id = source_config.get("id", "?")
            try:
                pipeline = self.build_source_pipeline(source_config)
                if self._start_pipeline(pipeline, source_id):
                    self._pipelines[source_id] = pipeline
                    started += 1
                    log.info("Preview started for %s", source_id)
                else:
                    self._stop_pipeline(pipeline)
                    log.warning("Preview failed to start for %s", source_id)
            except Exception as e:
                log.error("Preview failed for %s: %s", source_id, e, exc_info=True)

        self._is_running = started > 0
        if not self._is_running:
            self._last_error = self._last_error or "Could not start any preview pipeline"
            log.error("%s", self._last_error)

    def stop_idle(self):
        for source_id, pipeline in list(self._pipelines.items()):
            self._stop_pipeline(pipeline)
            log.info("Stopped preview for %s", source_id)
        self._pipelines.clear()
        self._is_running = False

    def add_preview_branch(self, pipeline: Gst.Pipeline, tee: Gst.Element, prefix: str = "preview"):
        """Tee branch for live preview during recording."""
        queue = Gst.ElementFactory.make("queue", f"{prefix}-queue")
        queue.set_property("max-size-buffers", 1)
        queue.set_property("leaky", 2)

        videoconvert = Gst.ElementFactory.make("videoconvert", f"{prefix}-convert")
        videoscale = Gst.ElementFactory.make("videoscale", f"{prefix}-scale")
        caps_filter = Gst.ElementFactory.make("capsfilter", f"{prefix}-caps")
        caps_filter.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw,width=1280,height=720"),
        )
        jpegenc = self._make_jpeg_enc(f"{prefix}-jpegenc")
        appsink = Gst.ElementFactory.make("appsink", f"{prefix}-sink")
        appsink.set_property("emit-signals", True)
        appsink.set_property("max-buffers", 1)
        appsink.set_property("drop", True)
        appsink.set_property("sync", False)

        def handler(appsink_el) -> Gst.FlowReturn:
            sample = appsink_el.emit("pull-sample")
            if not sample:
                return Gst.FlowReturn.OK
            buf = sample.get_buffer()
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if ok:
                try:
                    jpeg = bytes(mapinfo.data)
                    with self._jpeg_lock:
                        self._latest_jpeg = jpeg
                    self._on_jpeg(jpeg)
                    self._push_to_subscribers("__composite__", jpeg)
                finally:
                    buf.unmap(mapinfo)
            return Gst.FlowReturn.OK

        appsink.connect("new-sample", handler)

        for el in (queue, videoconvert, videoscale, caps_filter, jpegenc, appsink):
            pipeline.add(el)

        tee_pad = tee.request_pad_simple("src_%u")
        tee_pad.link(queue.get_static_pad("sink"))
        queue.link(videoconvert)
        videoconvert.link(videoscale)
        videoscale.link(caps_filter)
        caps_filter.link(jpegenc)
        jpegenc.link(appsink)
