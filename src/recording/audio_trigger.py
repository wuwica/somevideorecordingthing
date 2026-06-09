"""Audio fingerprint-based trigger for auto-stop functionality."""
import logging
import os
import threading
import time
from typing import Optional, Callable

log = logging.getLogger(__name__)

import numpy as np

try:
    from scipy.io import wavfile
    from scipy import signal as scipy_signal
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


class AudioTrigger:
    """
    Detects a reference audio clip in the live audio stream via spectral
    fingerprinting and triggers a callback when a match is found.

    The incoming stream is expected as raw S16LE PCM at 48 000 Hz (stereo),
    which matches the recording pipeline's audio format.
    """

    TARGET_SAMPLE_RATE = 48_000
    _NPERSEG = 1024
    _HOP = 512

    def __init__(
        self,
        reference_clip_path: str,
        threshold: float = 0.88,
        check_interval: float = 0.5,
    ):
        self.reference_clip_path = reference_clip_path
        self.threshold = threshold
        self.check_interval = check_interval

        self.reference_fingerprint: Optional[np.ndarray] = None
        self.ref_samples: int = 0

        self._buffer = np.array([], dtype=np.float32)
        self._buffer_lock = threading.Lock()
        # Keep at most 15 s of audio in the rolling buffer
        self._max_buffer_samples = self.TARGET_SAMPLE_RATE * 15

        self.callback: Optional[Callable] = None
        self.is_monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None

        self._load_reference()

    # ------------------------------------------------------------------ loading

    def _load_reference(self):
        if not self.reference_clip_path or not os.path.exists(self.reference_clip_path):
            return
        if not SCIPY_AVAILABLE:
            log.warning("scipy not available – audio trigger disabled")
            return
        try:
            rate, data = wavfile.read(self.reference_clip_path)
            mono = self._to_mono_float(data)
            if rate != self.TARGET_SAMPLE_RATE:
                n_out = int(len(mono) * self.TARGET_SAMPLE_RATE / rate)
                mono = scipy_signal.resample(mono, n_out)
            self.ref_samples = len(mono)
            self.reference_fingerprint = self._spectral_fingerprint(mono)
            duration = self.ref_samples / self.TARGET_SAMPLE_RATE
            log.info("Loaded '%s' (%.2fs)", os.path.basename(self.reference_clip_path), duration)
        except Exception as exc:
            log.error("Failed to load reference clip: %s", exc, exc_info=True)

    @staticmethod
    def _to_mono_float(data: np.ndarray) -> np.ndarray:
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32_768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2_147_483_648.0
        else:
            data = data.astype(np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data

    # ---------------------------------------------------------------- fingerprint

    def _spectral_fingerprint(self, audio: np.ndarray) -> np.ndarray:
        """
        Compute a normalised log-power spectrum fingerprint.

        Overlapping Hann-windowed FFT frames are averaged over time, then
        log-compressed and L2-normalised so cosine similarity equals the
        dot product of two fingerprints.
        """
        nperseg = self._NPERSEG
        hop = self._HOP
        if len(audio) < nperseg:
            audio = np.pad(audio, (0, nperseg - len(audio)))

        window = np.hanning(nperseg)
        frames = []
        for start in range(0, len(audio) - nperseg + 1, hop):
            fft = np.abs(np.fft.rfft(audio[start:start + nperseg] * window))
            frames.append(fft)

        if not frames:
            return np.zeros(nperseg // 2 + 1, dtype=np.float32)

        spectrum = np.mean(frames, axis=0).astype(np.float32)
        spectrum = np.log1p(spectrum)
        norm = float(np.linalg.norm(spectrum))
        if norm > 1e-10:
            spectrum /= norm
        return spectrum

    # ----------------------------------------------------------------- ingestion

    def add_audio_chunk(self, pcm_bytes: bytes, channels: int = 2):
        """
        Receive a raw S16LE PCM chunk from the recording pipeline.
        Called from the GStreamer appsink new-sample callback.
        """
        if not self.is_monitoring:
            return
        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32_768.0
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1)
            with self._buffer_lock:
                self._buffer = np.concatenate([self._buffer, samples])
                if len(self._buffer) > self._max_buffer_samples:
                    self._buffer = self._buffer[-self._max_buffer_samples:]
        except Exception as exc:
            log.error("add_audio_chunk error: %s", exc)

    # --------------------------------------------------------------- monitoring

    def set_callback(self, callback: Callable):
        self.callback = callback

    def start_monitoring(self):
        if self.is_monitoring or self.reference_fingerprint is None:
            return
        self.is_monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self):
        self.is_monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        with self._buffer_lock:
            self._buffer = np.array([], dtype=np.float32)

    def _monitor_loop(self):
        min_required = max(self.ref_samples, self.TARGET_SAMPLE_RATE)
        window_len = self.ref_samples if self.ref_samples > 0 else self.TARGET_SAMPLE_RATE
        # Slide in steps of 25 % of the reference length for decent coverage
        hop = max(window_len // 4, 1)

        while self.is_monitoring:
            time.sleep(self.check_interval)

            with self._buffer_lock:
                buf = self._buffer.copy()

            if len(buf) < min_required:
                continue

            # Search within the most recent 5 s to stay responsive
            search = buf[-min(len(buf), self.TARGET_SAMPLE_RATE * 5):]
            best = 0.0
            end = max(1, len(search) - window_len + 1)
            for start in range(0, end, hop):
                window = search[start:start + window_len]
                if len(window) < window_len // 2:
                    continue
                fp = self._spectral_fingerprint(window)
                # Both vectors are L2-normalised, so dot = cosine similarity
                sim = float(np.dot(fp, self.reference_fingerprint))
                if sim > best:
                    best = sim

            if best >= self.threshold:
                log.info("Match detected (similarity=%.3f)", best)
                if self.callback:
                    self.callback()
                self.stop_monitoring()

    # ---------------------------------------------------------------- properties

    @property
    def is_ready(self) -> bool:
        """True if a valid reference fingerprint is loaded."""
        return self.reference_fingerprint is not None

    def status_dict(self) -> dict:
        return {
            "ready": self.is_ready,
            "clip_path": self.reference_clip_path,
            "threshold": self.threshold,
            "check_interval": self.check_interval,
            "ref_duration": round(self.ref_samples / self.TARGET_SAMPLE_RATE, 2) if self.ref_samples else 0,
        }
