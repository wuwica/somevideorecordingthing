"""GStreamer video capture pipeline for multiple video sources."""
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib
from typing import Optional, Callable, Dict
import threading


class VideoCapture:
    """Manages GStreamer pipeline for a single video source."""
    
    def __init__(self, device_path: str, source_id: str, callback: Optional[Callable] = None):
        """
        Initialize video capture.
        
        Args:
            device_path: Path to video device (e.g., /dev/video0)
            source_id: Unique identifier for this source
            callback: Optional callback function for frame data
        """
        self.device_path = device_path
        self.source_id = source_id
        self.callback = callback
        self.pipeline: Optional[Gst.Pipeline] = None
        self.appsink: Optional[GstApp.AppSink] = None
        self.loop: Optional[GLib.MainLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.is_running = False
        
        Gst.init(None)
    
    def build_pipeline(self) -> Gst.Pipeline:
        """Build GStreamer pipeline for video capture."""
        pipeline = Gst.Pipeline.new(f"video-capture-{self.source_id}")
        
        # Video source
        source = Gst.ElementFactory.make("v4l2src", f"source-{self.source_id}")
        source.set_property("device", self.device_path)
        
        # Try to use hardware decoder if available, otherwise raw video
        # For HDMI capture cards that output H.264
        caps = Gst.Caps.from_string("video/x-raw,format=YUY2,width=1920,height=1080,framerate=30/1")
        
        # Video converter
        videoconvert = Gst.ElementFactory.make("videoconvert", f"convert-{self.source_id}")
        
        # Video scaler (optional, for resizing)
        videoscale = Gst.ElementFactory.make("videoscale", f"scale-{self.source_id}")
        
        # Video format
        caps_filter = Gst.ElementFactory.make("capsfilter", f"caps-{self.source_id}")
        caps_filter.set_property("caps", caps)
        
        # App sink for frame extraction (if callback provided)
        if self.callback:
            self.appsink = Gst.ElementFactory.make("appsink", f"sink-{self.source_id}")
            self.appsink.set_property("emit-signals", True)
            self.appsink.set_property("max-buffers", 1)
            self.appsink.set_property("drop", True)
            self.appsink.connect("new-sample", self._on_new_sample)
            
            pipeline.add(source)
            pipeline.add(videoconvert)
            pipeline.add(videoscale)
            pipeline.add(caps_filter)
            pipeline.add(self.appsink)
            
            source.link(videoconvert)
            videoconvert.link(videoscale)
            videoscale.link(caps_filter)
            caps_filter.link(self.appsink)
        else:
            # For composition pipeline, create a source pad
            pipeline.add(source)
            pipeline.add(videoconvert)
            pipeline.add(videoscale)
            pipeline.add(caps_filter)
            
            source.link(videoconvert)
            videoconvert.link(videoscale)
            videoscale.link(caps_filter)
        
        return pipeline
    
    def _on_new_sample(self, appsink: GstApp.AppSink) -> Gst.FlowReturn:
        """Handle new sample from appsink."""
        sample = appsink.emit("pull-sample")
        if sample and self.callback:
            buffer = sample.get_buffer()
            caps = sample.get_caps()
            structure = caps.get_structure(0)
            width = structure.get_int("width").value
            height = structure.get_int("height").value
            
            # Extract frame data
            success, map_info = buffer.map(Gst.MapFlags.READ)
            if success:
                try:
                    frame_data = map_info.data
                    self.callback(self.source_id, frame_data, width, height)
                finally:
                    buffer.unmap(map_info)
        
        return Gst.FlowReturn.OK
    
    def start(self):
        """Start the video capture pipeline."""
        if self.is_running:
            return
        
        self.pipeline = self.build_pipeline()
        
        # Set pipeline to playing state
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(f"Failed to start video capture pipeline for {self.source_id}")
        
        self.is_running = True
    
    def stop(self):
        """Stop the video capture pipeline."""
        if not self.is_running or not self.pipeline:
            return
        
        self.pipeline.set_state(Gst.State.NULL)
        self.is_running = False
        self.pipeline = None
    
    def get_source_pad(self) -> Optional[Gst.Pad]:
        """Get the source pad for composition pipeline."""
        if not self.pipeline or not self.is_running:
            return None
        
        # Find the caps filter element and get its source pad
        caps_filter = self.pipeline.get_by_name(f"caps-{self.source_id}")
        if caps_filter:
            return caps_filter.get_static_pad("src")
        return None


class VideoCaptureManager:
    """Manages multiple video capture sources."""
    
    def __init__(self):
        self.captures: Dict[str, VideoCapture] = {}
    
    def add_source(self, device_path: str, source_id: str, callback: Optional[Callable] = None) -> VideoCapture:
        """Add a video capture source."""
        capture = VideoCapture(device_path, source_id, callback)
        self.captures[source_id] = capture
        return capture
    
    def start_all(self):
        """Start all video captures."""
        for capture in self.captures.values():
            capture.start()
    
    def stop_all(self):
        """Stop all video captures."""
        for capture in self.captures.values():
            capture.stop()
    
    def get_capture(self, source_id: str) -> Optional[VideoCapture]:
        """Get a specific capture by ID."""
        return self.captures.get(source_id)
    
    def remove_source(self, source_id: str):
        """Remove a video capture source."""
        if source_id in self.captures:
            self.captures[source_id].stop()
            del self.captures[source_id]

