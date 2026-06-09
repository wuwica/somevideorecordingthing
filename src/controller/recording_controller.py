"""Thread-safe recording controller shared by the Qt UI and the web server."""
import logging
import os
import threading
from typing import Optional

log = logging.getLogger(__name__)

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

from src.capture.device_manager import DeviceManager
from src.capture.hdmi_rx import prepare_hdmi_rx
from src.controller.app_state import AppState
from src.preview.preview_manager import PreviewManager
from src.recording.recorder import Recorder
from src.recording.trigger_manager import TriggerManager
from src.usb.usb_monitor import USBMonitor


class RecordingController(QObject):
    """Owns all non-UI state and exposes thread-safe control methods.

    PyQt6 automatically queues cross-thread signal delivery, so signals emitted
    from GStreamer/USB/trigger threads are safely received on the Qt main thread.
    """

    recording_started = pyqtSignal(str)   # output_path
    recording_stopped = pyqtSignal(str)   # output_path
    usb_inserted = pyqtSignal(dict)
    usb_removed = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    preview_frame_ready = pyqtSignal()

    def __init__(self, config_path: str, web_port: int = 8080):
        super().__init__()
        self._lock = threading.RLock()
        self._state = AppState()
        self._config_path = config_path
        self.web_port = web_port
        self._sse_bus = None  # injected after web server starts

        Gst.init(None)

        self._device_manager = DeviceManager()
        self._trigger_manager = TriggerManager()
        self._recorder = Recorder(config_path)
        self._recorder.set_trigger_manager(self._trigger_manager)
        self._preview_manager = PreviewManager(
            get_config=lambda: self._recorder.layout_engine.config,
            on_jpeg=self._on_preview_jpeg,
        )
        self._recorder.set_preview_manager(self._preview_manager)

        self._usb_monitor = USBMonitor(
            on_usb_inserted=self._on_usb_inserted_raw,
            on_usb_removed=self._on_usb_removed_raw,
        )

        self._setup_devices()
        self._setup_triggers_from_config()
        self._trigger_manager.set_callback(self._on_trigger_fired)
        self._usb_monitor.start_monitoring()

    # ------------------------------------------------------------------ setup

    def _setup_devices(self):
        self._device_manager.detect_video_devices()
        self._sync_config_devices()
        sources, sinks = self._device_manager.detect_audio_devices()
        mic = sources[0].name if sources else None
        game = sinks[0].name if sinks else None
        self._recorder.setup_audio(mic, game)

    def _prepare_hdmi_sources(self, config: dict):
        """Query DV timings on Rockchip HDMI-RX before building pipelines."""
        for source in config.get("sources", []):
            if source.get("type") != "hdmi":
                continue
            device_path = source.get("device")
            if not device_path:
                continue
            info = prepare_hdmi_rx(device_path)
            if not info:
                source["hdmi_signal"] = False
                continue
            source["hdmi_signal"] = info.has_signal
            source["capture_width"] = info.width
            source["capture_height"] = info.height
            source["capture_format"] = info.pixel_format
            if info.has_signal:
                hdmi_dev = self._device_manager.find_device(device_path)
                if hdmi_dev:
                    hdmi_dev.gst_ready = self._device_manager.probe_device(
                        device_path,
                        info.width,
                        info.height,
                        formats=hdmi_dev.formats,
                    )

    def _sync_config_devices(self):
        """Map detected V4L2 devices onto layout source entries."""
        config = self._recorder.layout_engine.config
        hdmi = self._device_manager.get_hdmi_device()
        usb_cams = self._device_manager.get_usb_cameras()
        self._ensure_config_sources(config, hdmi, usb_cams)
        usb_idx = 0
        mapped_paths: set[str] = set()

        for source in config.get("sources", []):
            source_type = source.get("type", "")
            source_id = source.get("id", "?")

            if source_type == "hdmi":
                if hdmi:
                    source["device"] = hdmi.device_path
                    source["capabilities"] = hdmi.capabilities
                    source["formats"] = hdmi.formats
                    mapped_paths.add(hdmi.device_path)
                    log.info("%s → %s", source_id, hdmi.device_path)
                else:
                    log.warning("No HDMI device for %s", source_id)
                    source.pop("hdmi_signal", None)
            elif source_type == "usb":
                if usb_idx < len(usb_cams):
                    cam = usb_cams[usb_idx]
                    source["device"] = cam.device_path
                    source["capabilities"] = cam.capabilities
                    source["formats"] = cam.formats
                    mapped_paths.add(cam.device_path)
                    log.info("%s → %s (%s)", source_id, cam.device_path, cam.name)
                    usb_idx += 1
                else:
                    log.warning("No USB camera for %s", source_id)

        # Auto-add detected USB cameras not already in config
        output = config.get("output", {})
        out_w = output.get("width", 1920)
        cam_w, cam_h = 320, 240
        cam_x = max(0, out_w - cam_w)
        cam_num = sum(1 for s in config.get("sources", []) if s.get("type") == "usb") + 1
        for cam in usb_cams:
            if cam.device_path in mapped_paths:
                continue
            source_id = f"camera{cam_num}"
            config.setdefault("sources", []).append({
                "id": source_id,
                "type": "usb",
                "device": cam.device_path,
                "capabilities": cam.capabilities,
                "formats": cam.formats,
                "position": {"x": cam_x, "y": 0},
                "size": {"width": cam_w, "height": cam_h},
                "z_order": cam_num,
            })
            mapped_paths.add(cam.device_path)
            log.info("Auto-added %s → %s (%s)", source_id, cam.device_path, cam.name)
            cam_num += 1

        self._apply_recording_layout(config)
        self._prepare_hdmi_sources(config)

        active = [
            s for s in config.get("sources", [])
            if s.get("device") and os.path.exists(s["device"])
        ]
        log.info("%d active source(s) in config", len(active))

    def _ensure_config_sources(self, config: dict, hdmi, usb_cams: list):
        """Create config source entries from detected hardware when missing."""
        sources = config.setdefault("sources", [])
        if not sources and not hdmi and not usb_cams:
            log.warning("No video hardware detected")
            return

        if hdmi and not any(s.get("type") == "hdmi" for s in sources):
            sources.insert(0, {
                "id": "hdmi",
                "type": "hdmi",
                "device": hdmi.device_path,
                "capabilities": hdmi.capabilities,
                "formats": hdmi.formats,
            })
            log.info("Auto-added hdmi → %s", hdmi.device_path)

        if not sources and usb_cams:
            for i, cam in enumerate(usb_cams, 1):
                sources.append({
                    "id": f"camera{i}",
                    "type": "usb",
                    "device": cam.device_path,
                    "capabilities": cam.capabilities,
                    "formats": cam.formats,
                })
                log.info("Auto-added camera%d → %s", i, cam.device_path)

    def _apply_recording_layout(self, config: dict):
        """Set default position/size for sources that have no saved layout."""
        output = config.get("output", {})
        out_w = output.get("width", 1920)
        out_h = output.get("height", 1080)
        cam_w, cam_h = 320, 240
        cam_x = max(0, out_w - cam_w)
        z = 0
        for source in config.get("sources", []):
            if source.get("type") == "hdmi":
                source.setdefault("position", {"x": 0, "y": 0})
                source.setdefault("size", {"width": out_w, "height": out_h})
                source.setdefault("z_order", 0)
            elif source.get("type") == "usb":
                z += 1
                source.setdefault("position", {"x": cam_x, "y": 0})
                source.setdefault("size", {"width": cam_w, "height": cam_h})
                source.setdefault("z_order", z)

    def start_preview(self):
        """Start per-device preview pipelines (call after Qt/GLib loop is running)."""
        self._preview_manager.start_idle()

    def _restart_preview(self):
        """Restart idle preview pipelines. Safe to call from any thread."""
        if self._state.is_recording:
            return
        self._preview_manager.stop_idle()
        self._preview_manager.start_idle()

    def _restart_preview_bg(self):
        """Schedule a preview restart on a daemon thread so callers don't block."""
        t = threading.Thread(target=self._restart_preview, daemon=True,
                             name="preview-restart")
        t.start()

    def _setup_triggers_from_config(self):
        config = self._recorder.layout_engine.config
        stop_frame = config.get("stop_frame", {})
        if stop_frame.get("enabled", False):
            img = stop_frame.get("image", "")
            if img and os.path.exists(img):
                self._trigger_manager.configure_frame(
                    img,
                    threshold=stop_frame.get("threshold", 0.85),
                    check_interval=stop_frame.get("check_interval", 1.0),
                )
        audio_trigger_cfg = config.get("audio_trigger", {})
        if audio_trigger_cfg.get("enabled", False):
            clip = audio_trigger_cfg.get("clip", "")
            if clip and os.path.exists(clip):
                self._trigger_manager.configure_audio(
                    clip,
                    threshold=audio_trigger_cfg.get("threshold", 0.88),
                    check_interval=audio_trigger_cfg.get("check_interval", 0.5),
                )

    # ------------------------------------------------------------------ raw callbacks (any thread)

    def _on_usb_inserted_raw(self, device_info: dict):
        with self._lock:
            if device_info.get("mount_point"):
                self._state.usb_mount_point = device_info["mount_point"]
        self._publish_sse("usb_inserted", device_info)
        # PyQt6 auto-queues cross-thread signal emissions
        self.usb_inserted.emit(device_info)

    def _on_usb_removed_raw(self, device_info: dict):
        with self._lock:
            self._state.usb_mount_point = None
        self._publish_sse("usb_removed", device_info)
        self.usb_removed.emit(device_info)

    def _on_trigger_fired(self):
        """Called from AudioTrigger or FrameDetector monitor thread."""
        ok, msg = self.stop_recording()
        if not ok:
            log.error("Trigger fired but stop failed: %s", msg)

    def _on_preview_jpeg(self, _jpeg: bytes):
        self.preview_frame_ready.emit()

    # ------------------------------------------------------------------ public API (any thread)

    def start_recording(self) -> tuple[bool, str]:
        with self._lock:
            if self._state.is_recording:
                return False, "already recording"
            mount = self._state.usb_mount_point or self._usb_monitor.get_first_mount_point()
            if mount:
                subpath = self._recorder.layout_engine.config.get("output", {}).get("output_subpath", "")
                output_dir = os.path.join(mount, subpath) if subpath else mount
                os.makedirs(output_dir, exist_ok=True)
                self._recorder.output_directory = output_dir
            try:
                self._prepare_hdmi_sources(self._recorder.layout_engine.config)
                self._preview_manager.stop_idle()
                self._recorder.start_recording()
                self._state.is_recording = True
                self._state.output_path = self._recorder.get_output_path()
                path = self._state.output_path or ""
            except Exception as e:
                self._preview_manager.start_idle()
                err_msg = str(e)
                self.error_occurred.emit(err_msg)
                self._publish_sse("error", {"message": err_msg})
                return False, err_msg

        self._publish_sse("recording_started", self.get_status())
        self.recording_started.emit(path)
        return True, path

    def stop_recording(self) -> tuple[bool, str]:
        with self._lock:
            if not self._state.is_recording:
                return False, "not recording"
            path = self._state.output_path or ""
            try:
                self._recorder.stop_recording()
                self._state.is_recording = False
                self._state.output_path = None
            except Exception as e:
                return False, str(e)

        self._preview_manager.start_idle()
        self._publish_sse("recording_stopped", self.get_status())
        self.recording_stopped.emit(path)
        return True, path

    def get_status(self) -> dict:
        with self._lock:
            return {
                "is_recording": self._state.is_recording,
                "output_path": self._state.output_path,
                "usb_mount_point": self._state.usb_mount_point,
                "usb_devices": self._usb_monitor.get_mounted_devices(),
                "active_trigger": self._trigger_manager.active_trigger,
                "trigger": self._trigger_manager.status_dict(),
            }

    def reload_audio_trigger(
        self,
        clip_path: str,
        threshold: float = 0.88,
        check_interval: float = 0.5,
    ) -> tuple[bool, str]:
        with self._lock:
            try:
                self._trigger_manager.reload_audio(clip_path, threshold, check_interval)
                self._publish_sse("trigger_updated", self.get_status())
                return True, "ok"
            except Exception as e:
                return False, str(e)

    def disable_audio_trigger(self) -> tuple[bool, str]:
        with self._lock:
            try:
                if self._trigger_manager.audio_trigger:
                    self._trigger_manager.audio_trigger.stop_monitoring()
                    self._trigger_manager.audio_trigger = None
                    if self._trigger_manager.active_trigger == "audio":
                        self._trigger_manager._active = None
                self._publish_sse("trigger_updated", self.get_status())
                return True, "ok"
            except Exception as e:
                return False, str(e)

    def reload_frame_trigger(
        self,
        image_path: str,
        threshold: float = 0.85,
        check_interval: float = 1.0,
    ) -> tuple[bool, str]:
        with self._lock:
            try:
                self._trigger_manager.reload_frame(image_path, threshold, check_interval)
                self._publish_sse("trigger_updated", self.get_status())
                return True, "ok"
            except Exception as e:
                return False, str(e)

    def disable_frame_trigger(self) -> tuple[bool, str]:
        with self._lock:
            try:
                if self._trigger_manager.frame_detector:
                    self._trigger_manager.frame_detector.stop_monitoring()
                    self._trigger_manager.frame_detector = None
                    if self._trigger_manager.active_trigger == "frame":
                        self._trigger_manager._active = None
                self._publish_sse("trigger_updated", self.get_status())
                return True, "ok"
            except Exception as e:
                return False, str(e)

    def get_config(self) -> dict:
        """Return the live layout config (sources + output + overlays)."""
        config = self._recorder.layout_engine.config
        overlays = []
        for i, o in enumerate(config.get("overlays", [])):
            ovl = {k: v for k, v in o.items()}
            img = ovl.get("image", "")
            if img:
                ovl["image_url"] = "/overlays/" + os.path.basename(img)
            ovl["index"] = i
            overlays.append(ovl)
        return {
            "sources": [
                {k: v for k, v in s.items()
                 if k not in ("capabilities", "formats", "hdmi_signal",
                              "capture_width", "capture_height", "capture_format")}
                for s in config.get("sources", [])
            ],
            "output": config.get("output", {}),
            "overlays": overlays,
        }

    def update_config(self, sources: list | None, output: dict | None) -> tuple[bool, str]:
        """Merge source/output changes into the live config, save, and restart preview."""
        with self._lock:
            if self._state.is_recording:
                return False, "Cannot change config while recording"
            config = self._recorder.layout_engine.config

            if sources is not None:
                src_by_id = {s.get("id"): s for s in config.get("sources", [])}
                for patch in sources:
                    sid = patch.get("id")
                    if sid and sid in src_by_id:
                        existing = src_by_id[sid]
                        for key in ("position", "size", "z_order", "fps",
                                    "rotation", "mask_shape", "mask_points",
                                    "mask_position", "mask_size"):
                            if key in patch:
                                existing[key] = patch[key]
                        log.info("Updated source %s: %s", sid,
                                 {k: patch[k] for k in (
                                     "position", "size", "z_order", "fps",
                                     "rotation", "mask_shape", "mask_points",
                                     "mask_position", "mask_size") if k in patch})

            if output is not None:
                config.setdefault("output", {}).update(
                    {k: output[k] for k in ("width", "height", "fps") if k in output}
                )
                log.info("Updated output: %s", output)

            try:
                self._recorder.layout_engine.save_config()
            except Exception as e:
                return False, f"Failed to save config: {e}"

        # Restart preview outside the lock on a daemon thread so the HTTP
        # response returns immediately (GStreamer pipeline init can block 10s+).
        self._restart_preview_bg()
        return True, "ok"

    def upload_overlay(
        self,
        filename: str,
        data: bytes,
        position: dict,
        size: dict,
        opacity: float,
    ) -> tuple[bool, str]:
        with self._lock:
            if self._state.is_recording:
                return False, "Cannot modify overlays while recording"
            overlays_dir = os.path.join(os.path.dirname(self._config_path), "overlays")
            os.makedirs(overlays_dir, exist_ok=True)

            base, ext = os.path.splitext(filename)
            save_path = os.path.join(overlays_dir, filename)
            n = 1
            while os.path.exists(save_path):
                save_path = os.path.join(overlays_dir, f"{base}_{n}{ext}")
                n += 1

            with open(save_path, "wb") as f:
                f.write(data)

            config = self._recorder.layout_engine.config
            ovl_cfg = {
                "image": save_path,
                "position": position,
                "size": size,
                "opacity": opacity,
                "z_order": 100 + len(config.get("overlays", [])),
            }
            config.setdefault("overlays", []).append(ovl_cfg)
            self._recorder.overlay_manager.overlays_config = config["overlays"]

            try:
                self._recorder.layout_engine.save_config()
            except Exception as e:
                return False, f"Saved file but config write failed: {e}"
        log.info("Overlay uploaded: %s", save_path)
        return True, save_path

    def remove_overlay(self, index: int) -> tuple[bool, str]:
        with self._lock:
            if self._state.is_recording:
                return False, "Cannot modify overlays while recording"
            config = self._recorder.layout_engine.config
            overlays = config.get("overlays", [])
            if index < 0 or index >= len(overlays):
                return False, f"Overlay index {index} out of range"
            del overlays[index]
            self._recorder.overlay_manager.overlays_config = overlays
            try:
                self._recorder.layout_engine.save_config()
            except Exception as e:
                return False, f"Config write failed: {e}"
        return True, "ok"

    def update_overlay(self, index: int, patch: dict) -> tuple[bool, str]:
        with self._lock:
            if self._state.is_recording:
                return False, "Cannot modify overlays while recording"
            config = self._recorder.layout_engine.config
            overlays = config.get("overlays", [])
            if index < 0 or index >= len(overlays):
                return False, f"Overlay index {index} out of range"
            for key in ("position", "size", "opacity", "z_order"):
                if key in patch:
                    overlays[index][key] = patch[key]
            self._recorder.overlay_manager.overlays_config = overlays
            try:
                self._recorder.layout_engine.save_config()
            except Exception as e:
                return False, f"Config write failed: {e}"
        return True, "ok"

    def get_devices(self) -> dict:
        config_sources = [
            {
                "id": s.get("id"),
                "type": s.get("type"),
                "device": s.get("device"),
            }
            for s in self._recorder.layout_engine.config.get("sources", [])
        ]
        return {
            "configured_sources": config_sources,
            "video": [
                {
                    "path": d.device_path,
                    "name": d.name,
                    "type": d.device_type,
                    "formats": d.formats,
                    "capabilities": d.capabilities,
                    "driver": d.driver,
                    "parent": d.parent_label,
                    "gst_ready": d.gst_ready,
                    "framerates": self._device_manager.list_framerates(d.device_path),
                }
                for d in self._device_manager.video_devices
            ],
            "audio_sources": [
                {"name": s.name, "description": s.description}
                for s in self._device_manager.audio_sources
            ],
            "audio_sinks": [
                {"name": s.name, "description": s.description}
                for s in self._device_manager.audio_sinks
            ],
        }

    def refresh_devices(self) -> dict:
        with self._lock:
            self._setup_devices()
        self._restart_preview_bg()
        return self.get_devices()

    def get_preview_jpeg(self) -> Optional[bytes]:
        return self._preview_manager.get_latest_jpeg()

    def get_source_preview_jpeg(self, source_id: str) -> Optional[bytes]:
        return self._preview_manager.get_source_jpeg(source_id)

    def list_preview_sources(self) -> list:
        sources = self._preview_manager.list_sources()
        if sources:
            return sources

        fallback = []
        for src in self._recorder.layout_engine.config.get("sources", []):
            sid = src.get("id")
            if not sid:
                continue
            fallback.append({
                "id": sid,
                "type": src.get("type", "unknown"),
                "device": src.get("device", ""),
                "formats": src.get("formats", []),
                "has_frame": False,
                "preview_running": False,
                "hdmi_signal": src.get("hdmi_signal"),
                "capture_size": None,
            })
        if fallback:
            return fallback

        for device in self._device_manager.video_devices:
            fallback.append({
                "id": device.device_id,
                "type": device.device_type,
                "device": device.device_path,
                "formats": device.formats,
                "has_frame": False,
                "preview_running": False,
                "hdmi_signal": None,
                "capture_size": None,
            })
        return fallback

    def get_preview_status(self) -> dict:
        return self._preview_manager.get_status()

    def get_preview_image(self) -> Optional[QImage]:
        jpeg = self.get_preview_jpeg()
        if not jpeg:
            return None
        image = QImage()
        if image.loadFromData(jpeg, "JPEG"):
            return image
        return None

    def shutdown(self):
        if self._state.is_recording:
            self.stop_recording()
        self._preview_manager.stop_idle()
        self._usb_monitor.stop_monitoring()

    def set_sse_bus(self, bus):
        self._sse_bus = bus

    @property
    def state(self) -> AppState:
        return self._state

    @property
    def trigger_manager(self) -> TriggerManager:
        return self._trigger_manager

    @property
    def usb_monitor(self) -> USBMonitor:
        return self._usb_monitor

    # ------------------------------------------------------------------ helpers

    def _publish_sse(self, event_type: str, data: dict):
        if self._sse_bus:
            self._sse_bus.publish(event_type, data)
