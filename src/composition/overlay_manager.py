"""Image overlay management for video composition."""
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from typing import List, Dict, Optional
import os


class OverlayManager:
    """Manages image overlays on video feed."""
    
    def __init__(self, overlays_config: List[Dict]):
        """
        Initialize overlay manager.
        
        Args:
            overlays_config: List of overlay configurations from JSON
        """
        self.overlays_config = overlays_config
        self.overlay_elements: List[Gst.Element] = []
        self.pipeline: Optional[Gst.Pipeline] = None
        
        Gst.init(None)
    
    def build_overlay_pipeline(self, video_src_pad: Gst.Pad) -> Gst.Pipeline:
        """
        Build overlay pipeline.
        
        Args:
            video_src_pad: Source pad from video composition
        
        Returns:
            GStreamer pipeline with overlays applied
        """
        # For now, use a simpler approach with imagefreeze or cairooverlay
        # This is a simplified version - full implementation would require
        # more complex GStreamer pipeline setup
        
        # If no overlays, return None to indicate passthrough
        if not self.overlays_config:
            return None
        
        pipeline = Gst.Pipeline.new("overlay-pipeline")
        
        # Process overlays - for now, we'll use a simplified approach
        # Full implementation would require cairooverlay with proper callbacks
        # This is a placeholder that can be extended
        
        # For production, you would:
        # 1. Use cairooverlay element with draw callbacks
        # 2. Or use imagefreeze + compositor for static overlays
        # 3. Or use textoverlay for text overlays
        
        return pipeline
    
    def add_overlay(self, image_path: str, position: Dict, size: Dict, opacity: float = 1.0):
        """Add a new overlay dynamically."""
        overlay_config = {
            "image": image_path,
            "position": position,
            "size": size,
            "opacity": opacity
        }
        self.overlays_config.append(overlay_config)
    
    def remove_overlay(self, index: int):
        """Remove an overlay by index."""
        if 0 <= index < len(self.overlays_config):
            del self.overlays_config[index]
    
    def update_overlay_position(self, index: int, x: int, y: int):
        """Update overlay position."""
        if 0 <= index < len(self.overlays_config):
            self.overlays_config[index]["position"] = {"x": x, "y": y}
    
    def update_overlay_opacity(self, index: int, opacity: float):
        """Update overlay opacity."""
        if 0 <= index < len(self.overlays_config):
            self.overlays_config[index]["opacity"] = opacity
    
    def get_overlay_config(self) -> List[Dict]:
        """Get overlay configuration."""
        return self.overlays_config

