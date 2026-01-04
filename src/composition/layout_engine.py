"""Video layout and composition engine using GStreamer."""
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from typing import Dict, List, Optional
import json
import os


class LayoutEngine:
    """Manages video composition and layout based on JSON configuration."""
    
    def __init__(self, config_path: str):
        """
        Initialize layout engine.
        
        Args:
            config_path: Path to JSON configuration file
        """
        self.config_path = config_path
        self.config: Dict = {}
        self.compositor: Optional[Gst.Element] = None
        self.pipeline: Optional[Gst.Pipeline] = None
        
        Gst.init(None)
        self.load_config()
    
    def load_config(self):
        """Load layout configuration from JSON file."""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
        else:
            # Default configuration
            self.config = {
                "sources": [],
                "overlays": [],
                "output": {
                    "width": 1920,
                    "height": 1080,
                    "fps": 30
                }
            }
    
    def reload_config(self):
        """Reload configuration from file."""
        self.load_config()
    
    def build_composition_pipeline(self, video_sources: Dict[str, Gst.Pad]) -> Gst.Pipeline:
        """
        Build GStreamer composition pipeline.
        
        Args:
            video_sources: Dictionary mapping source IDs to their source pads
        
        Returns:
            GStreamer pipeline with compositor
        """
        pipeline = Gst.Pipeline.new("composition-pipeline")
        
        # Create compositor element
        self.compositor = Gst.ElementFactory.make("compositor", "compositor")
        
        # Set output format
        output_config = self.config.get("output", {})
        width = output_config.get("width", 1920)
        height = output_config.get("height", 1080)
        fps = output_config.get("fps", 30)
        
        caps = Gst.Caps.from_string(
            f"video/x-raw,width={width},height={height},framerate={fps}/1"
        )
        self.compositor.set_property("sink-pads", len(self.config.get("sources", [])))
        
        pipeline.add(self.compositor)
        
        # Link video sources to compositor
        for i, source_config in enumerate(self.config.get("sources", [])):
            source_id = source_config.get("id")
            if source_id in video_sources:
                source_pad = video_sources[source_id]
                
                # Get or request sink pad from compositor
                sink_pad_name = f"sink_{i}"
                sink_pad = self.compositor.get_request_pad(sink_pad_name)
                
                # Set pad properties for positioning and sizing
                position = source_config.get("position", {"x": 0, "y": 0})
                size = source_config.get("size", {"width": width, "height": height})
                z_order = source_config.get("z_order", i)
                
                sink_pad.set_property("xpos", position.get("x", 0))
                sink_pad.set_property("ypos", position.get("y", 0))
                sink_pad.set_property("width", size.get("width", width))
                sink_pad.set_property("height", size.get("height", height))
                sink_pad.set_property("zorder", z_order)
                
                # Link source pad to compositor sink pad
                source_pad.link(sink_pad)
        
        # Video converter after composition
        videoconvert = Gst.ElementFactory.make("videoconvert", "compositor-convert")
        caps_filter = Gst.ElementFactory.make("capsfilter", "compositor-caps")
        caps_filter.set_property("caps", caps)
        
        pipeline.add(videoconvert)
        pipeline.add(caps_filter)
        
        compositor_src = self.compositor.get_static_pad("src")
        compositor_src.link(videoconvert.get_static_pad("sink"))
        videoconvert.link(caps_filter)
        
        return pipeline
    
    def get_compositor_src_pad(self) -> Optional[Gst.Pad]:
        """Get the source pad from the compositor."""
        if not self.compositor:
            return None
        
        return self.compositor.get_static_pad("src")
    
    def update_source_position(self, source_id: str, x: int, y: int):
        """Update position of a video source."""
        for i, source_config in enumerate(self.config.get("sources", [])):
            if source_config.get("id") == source_id:
                source_config["position"] = {"x": x, "y": y}
                
                # Update pad properties if compositor exists
                if self.compositor:
                    sink_pad_name = f"sink_{i}"
                    sink_pad = self.compositor.get_static_pad(sink_pad_name)
                    if sink_pad:
                        sink_pad.set_property("xpos", x)
                        sink_pad.set_property("ypos", y)
                break
    
    def update_source_size(self, source_id: str, width: int, height: int):
        """Update size of a video source."""
        for i, source_config in enumerate(self.config.get("sources", [])):
            if source_config.get("id") == source_id:
                source_config["size"] = {"width": width, "height": height}
                
                # Update pad properties if compositor exists
                if self.compositor:
                    sink_pad_name = f"sink_{i}"
                    sink_pad = self.compositor.get_static_pad(sink_pad_name)
                    if sink_pad:
                        sink_pad.set_property("width", width)
                        sink_pad.set_property("height", height)
                break
    
    def get_source_config(self, source_id: str) -> Optional[Dict]:
        """Get configuration for a specific source."""
        for source_config in self.config.get("sources", []):
            if source_config.get("id") == source_id:
                return source_config
        return None

