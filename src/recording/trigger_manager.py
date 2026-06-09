"""Manages stop triggers: audio (priority) with frame detection as fallback."""
import logging
import os
from typing import Optional, Callable

log = logging.getLogger(__name__)

from src.recording.audio_trigger import AudioTrigger
from src.recording.frame_detector import FrameDetector


class TriggerManager:
    """
    Orchestrates stop triggers.

    Priority:
      1. AudioTrigger  – used when a reference clip is loaded and ready.
      2. FrameDetector – fallback when no audio clip is available.

    Both can be configured simultaneously; only one is active per recording
    session (decided at start_monitoring time).
    """

    def __init__(self):
        self.audio_trigger: Optional[AudioTrigger] = None
        self.frame_detector: Optional[FrameDetector] = None
        self._callback: Optional[Callable] = None
        self._active: Optional[str] = None  # "audio" | "frame" | None

    # ------------------------------------------------------------ configuration

    def configure_audio(
        self,
        clip_path: str,
        threshold: float = 0.88,
        check_interval: float = 0.5,
    ):
        self.audio_trigger = AudioTrigger(clip_path, threshold, check_interval)
        if self._callback:
            self.audio_trigger.set_callback(self._callback)

    def configure_frame(
        self,
        image_path: str,
        threshold: float = 0.85,
        check_interval: float = 1.0,
    ):
        if os.path.exists(image_path):
            self.frame_detector = FrameDetector(image_path, threshold, check_interval)
            if self._callback:
                self.frame_detector.set_callback(self._callback)
        else:
            log.warning("Frame image not found: %s", image_path)

    def set_callback(self, callback: Callable):
        self._callback = callback
        if self.audio_trigger:
            self.audio_trigger.set_callback(callback)
        if self.frame_detector:
            self.frame_detector.set_callback(callback)

    # ----------------------------------------------------------------- hot-reload

    def reload_audio(
        self,
        clip_path: str,
        threshold: Optional[float] = None,
        check_interval: Optional[float] = None,
    ):
        """Replace the audio trigger, optionally resuming monitoring if active."""
        was_active = self._active == "audio"
        if was_active and self.audio_trigger:
            self.audio_trigger.stop_monitoring()

        t = threshold if threshold is not None else (
            self.audio_trigger.threshold if self.audio_trigger else 0.88
        )
        i = check_interval if check_interval is not None else (
            self.audio_trigger.check_interval if self.audio_trigger else 0.5
        )
        self.configure_audio(clip_path, t, i)

        if was_active and self.audio_trigger and self.audio_trigger.is_ready:
            self.audio_trigger.start_monitoring()
            self._active = "audio"
        elif was_active:
            # New clip failed to load; fall back to frame if available
            self._active = None
            if self.frame_ready:
                self._active = "frame"
                self.frame_detector.start_monitoring()

    def reload_frame(
        self,
        image_path: str,
        threshold: Optional[float] = None,
        check_interval: Optional[float] = None,
    ):
        """Replace the frame detector, optionally resuming monitoring if active."""
        was_active = self._active == "frame"
        if was_active and self.frame_detector:
            self.frame_detector.stop_monitoring()

        t = threshold if threshold is not None else (
            self.frame_detector.threshold if self.frame_detector else 0.85
        )
        i = check_interval if check_interval is not None else (
            self.frame_detector.check_interval if self.frame_detector else 1.0
        )
        self.configure_frame(image_path, t, i)

        if was_active and self.frame_ready:
            self.frame_detector.start_monitoring()
            self._active = "frame"

    # ------------------------------------------------------------------ runtime

    @property
    def active_trigger(self) -> Optional[str]:
        return self._active

    @property
    def audio_ready(self) -> bool:
        return self.audio_trigger is not None and self.audio_trigger.is_ready

    @property
    def frame_ready(self) -> bool:
        return (
            self.frame_detector is not None
            and self.frame_detector.reference_frame is not None
        )

    def start_monitoring(self):
        if self.audio_ready:
            self._active = "audio"
            self.audio_trigger.start_monitoring()
            log.info("Using audio trigger (priority)")
        elif self.frame_ready:
            self._active = "frame"
            self.frame_detector.start_monitoring()
            log.info("Using frame trigger (no audio clip configured)")
        else:
            self._active = None
            log.info("No trigger configured, auto-stop disabled")

    def stop_monitoring(self):
        if self.audio_trigger:
            self.audio_trigger.stop_monitoring()
        if self.frame_detector:
            self.frame_detector.stop_monitoring()
        self._active = None

    def add_frame(self, frame_data: bytes, width: int, height: int):
        """Forward a video frame to the frame detector (when active)."""
        if self._active == "frame" and self.frame_detector:
            self.frame_detector.add_frame(frame_data, width, height)

    def add_audio_chunk(self, pcm_bytes: bytes, channels: int = 2):
        """Forward an audio chunk to the audio trigger (when active)."""
        if self._active == "audio" and self.audio_trigger:
            self.audio_trigger.add_audio_chunk(pcm_bytes, channels)

    def status_dict(self) -> dict:
        return {
            "active_trigger": self._active,
            "audio": self.audio_trigger.status_dict() if self.audio_trigger else {"loaded": False},
            "frame": {
                "ready": self.frame_ready,
                "image_path": self.frame_detector.reference_frame_path if self.frame_detector else None,
                "threshold": self.frame_detector.threshold if self.frame_detector else None,
                "check_interval": self.frame_detector.check_interval if self.frame_detector else None,
            } if self.frame_detector else {"loaded": False},
        }
