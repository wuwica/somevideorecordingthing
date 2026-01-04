"""Frame comparison for auto-stop functionality."""
import cv2
import numpy as np
from typing import Optional
import threading
import time


class FrameDetector:
    """Compares video frames against a reference frame for auto-stop."""
    
    def __init__(self, reference_frame_path: str, threshold: float = 0.85, check_interval: float = 1.0):
        """
        Initialize frame detector.
        
        Args:
            reference_frame_path: Path to reference frame image
            threshold: Similarity threshold (0.0 to 1.0)
            check_interval: Interval in seconds between frame checks
        """
        self.reference_frame_path = reference_frame_path
        self.threshold = threshold
        self.check_interval = check_interval
        self.reference_frame: Optional[np.ndarray] = None
        self.callback: Optional[callable] = None
        self.is_monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.frame_queue = []
        self.lock = threading.Lock()
        
        self.load_reference_frame()
    
    def load_reference_frame(self):
        """Load reference frame from file."""
        if self.reference_frame_path:
            try:
                img = cv2.imread(self.reference_frame_path)
                if img is not None:
                    # Convert to grayscale for comparison
                    self.reference_frame = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                else:
                    print(f"Warning: Could not load reference frame from {self.reference_frame_path}")
            except Exception as e:
                print(f"Error loading reference frame: {e}")
    
    def set_callback(self, callback: callable):
        """Set callback function to call when match is detected."""
        self.callback = callback
    
    def add_frame(self, frame_data: bytes, width: int, height: int):
        """
        Add a frame for comparison.
        
        Args:
            frame_data: Raw frame data (YUY2 format)
            width: Frame width
            height: Frame height
        """
        with self.lock:
            self.frame_queue.append((frame_data, width, height))
    
    def _compare_frame(self, frame: np.ndarray) -> float:
        """
        Compare frame against reference frame.
        
        Returns:
            Similarity score (0.0 to 1.0)
        """
        if self.reference_frame is None:
            return 0.0
        
        # Resize frame to match reference if needed
        ref_height, ref_width = self.reference_frame.shape[:2]
        frame_height, frame_width = frame.shape[:2]
        
        if frame_width != ref_width or frame_height != ref_height:
            frame = cv2.resize(frame, (ref_width, ref_height))
        
        # Use template matching
        result = cv2.matchTemplate(frame, self.reference_frame, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        
        return float(max_val)
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        while self.is_monitoring:
            frame_data = None
            width = 0
            height = 0
            
            # Get latest frame from queue
            with self.lock:
                if self.frame_queue:
                    frame_data, width, height = self.frame_queue.pop()
                    # Clear queue, only check latest frame
                    self.frame_queue.clear()
            
            if frame_data is not None:
                try:
                    # Convert YUY2 to grayscale
                    # YUY2 format: Y0 U0 Y1 V0 Y2 U2 Y3 V2 ...
                    yuy2_array = np.frombuffer(frame_data, dtype=np.uint8)
                    
                    # Extract Y (luminance) channel
                    y_channel = yuy2_array[0::2]  # Every other byte starting from 0
                    y_channel = y_channel[:width * height]  # Take only what we need
                    
                    # Reshape to image dimensions
                    frame_gray = y_channel.reshape((height, width))
                    
                    # Compare with reference
                    similarity = self._compare_frame(frame_gray)
                    
                    if similarity >= self.threshold:
                        print(f"Stop frame detected! Similarity: {similarity:.2f}")
                        if self.callback:
                            self.callback()
                        self.stop_monitoring()
                
                except Exception as e:
                    print(f"Error comparing frame: {e}")
            
            time.sleep(self.check_interval)
    
    def start_monitoring(self):
        """Start frame monitoring."""
        if self.is_monitoring:
            return
        
        if self.reference_frame is None:
            print("Warning: No reference frame loaded, cannot start monitoring")
            return
        
        self.is_monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop frame monitoring."""
        self.is_monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2.0)
        
        with self.lock:
            self.frame_queue.clear()

