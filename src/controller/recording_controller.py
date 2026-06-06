"""Thread-safe recording controller shared by the Qt UI and the web server."""
import os
import threading
from typing import Optional

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from PyQt6.QtCore import QObject, pyqtSignal

from src.capture.device_manager import DeviceManager
from src.controller.app_state import AppState
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
        hdmi = self._device_manager.get_hdmi_device()
        if hdmi:
            self._recorder.add_video_source(hdmi.device_path, "hdmi")
        for i, cam in enumerate(self._device_manager.get_usb_cameras(), 1):
            self._recorder.add_video_source(cam.device_path, f"camera{i}")
        sources, sinks = self._device_manager.detect_audio_devices()
        mic = sources[0].name if sources else None
        game = sinks[0].name if sinks else None
        self._recorder.setup_audio(mic, game)

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
            print(f"RecordingController: trigger fired but stop failed – {msg}")

    # ------------------------------------------------------------------ public API (any thread)

    def start_recording(self) -> tuple[bool, str]:
        with self._lock:
            if self._state.is_recording:
                return False, "already recording"
            mount = self._state.usb_mount_point or self._usb_monitor.get_first_mount_point()
            if mount:
                self._recorder.output_directory = mount
            try:
                self._recorder.start_recording()
                self._state.is_recording = True
                self._state.output_path = self._recorder.get_output_path()
                path = self._state.output_path or ""
            except Exception as e:
                return False, str(e)

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
                    if self._trigger_manager._active == "audio":
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
                    if self._trigger_manager._active == "frame":
                        self._trigger_manager._active = None
                self._publish_sse("trigger_updated", self.get_status())
                return True, "ok"
            except Exception as e:
                return False, str(e)

    def get_devices(self) -> dict:
        return {
            "video": [
                {"path": d.device_path, "name": d.name, "type": d.device_type}
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
        self._setup_devices()
        return self.get_devices()

    def shutdown(self):
        if self._state.is_recording:
            self.stop_recording()
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
