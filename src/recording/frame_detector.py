"""Frame comparison for auto-stop functionality using perceptual hashing."""
import threading
import time
from typing import Optional

import imagehash
import numpy as np
from PIL import Image


class FrameDetector:
    """Compares video frames against a reference frame for auto-stop.

    Uses perceptual hashing (pHash) which is robust to minor brightness/
    compression variation and orders of magnitude faster than template matching.
    Similarity is expressed as 1 - (hamming_distance / 64), so threshold=0.85
    means hamming distance ≤ 9 bits out of 64.
    """

    def __init__(self, reference_frame_path: str, threshold: float = 0.85, check_interval: float = 1.0):
        self.reference_frame_path = reference_frame_path
        self.threshold = threshold
        self.check_interval = check_interval

        self.reference_hash: Optional[imagehash.ImageHash] = None
        self.callback: Optional[callable] = None
        self.is_monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._frame_queue: list = []
        self._lock = threading.Lock()

        self.load_reference_frame()

    def load_reference_frame(self):
        if not self.reference_frame_path:
            return
        try:
            img = Image.open(self.reference_frame_path).convert("L")
            self.reference_hash = imagehash.phash(img)
        except Exception as e:
            print(f"FrameDetector: could not load reference frame – {e}")

    def set_callback(self, callback: callable):
        self.callback = callback

    def add_frame(self, frame_data: bytes, width: int, height: int):
        """Receive a raw YUY2 frame from the recording pipeline."""
        with self._lock:
            self._frame_queue.append((frame_data, width, height))

    def _similarity(self, frame_data: bytes, width: int, height: int) -> float:
        """Convert YUY2 → grayscale PIL Image, compute pHash similarity."""
        # Extract Y (luminance) channel: every other byte starting at 0
        yuy2 = np.frombuffer(frame_data, dtype=np.uint8)
        y = yuy2[0::2][:width * height].reshape((height, width))
        img = Image.fromarray(y, mode="L")
        frame_hash = imagehash.phash(img)
        distance = self.reference_hash - frame_hash  # Hamming distance (0–64)
        return 1.0 - distance / 64.0

    def _monitor_loop(self):
        while self.is_monitoring:
            frame_data = width = height = None
            with self._lock:
                if self._frame_queue:
                    frame_data, width, height = self._frame_queue[-1]
                    self._frame_queue.clear()

            if frame_data is not None:
                try:
                    similarity = self._similarity(frame_data, width, height)
                    if similarity >= self.threshold:
                        hamming = round((1.0 - similarity) * 64)
                        print(f"FrameDetector: stop frame detected (similarity={similarity:.3f}, hamming={hamming})")
                        if self.callback:
                            self.callback()
                        self.stop_monitoring()
                        return
                except Exception as e:
                    print(f"FrameDetector: comparison error – {e}")

            time.sleep(self.check_interval)

    def start_monitoring(self):
        if self.is_monitoring:
            return
        if self.reference_hash is None:
            print("FrameDetector: no reference hash loaded, cannot start monitoring")
            return
        self.is_monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self):
        self.is_monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        with self._lock:
            self._frame_queue.clear()

    # Keep backward-compat property name used by TriggerManager
    @property
    def reference_frame(self):
        return self.reference_hash
