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

    _RUNTIME_SOURCE_KEYS = frozenset({
        "capabilities", "formats", "hdmi_signal",
        "capture_width", "capture_height", "capture_format",
    })

    def save_config(self):
        """Persist the current in-memory config back to the JSON file.

        Runtime-only fields injected by device detection are stripped so the
        saved file stays clean and portable across reboots.
        """
        clean = {k: v for k, v in self.config.items() if k != "sources"}
        clean["sources"] = [
            {k: v for k, v in s.items() if k not in self._RUNTIME_SOURCE_KEYS}
            for s in self.config.get("sources", [])
        ]
        parent = os.path.dirname(os.path.abspath(self.config_path))
        os.makedirs(parent, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2)
            f.write("\n")
    
    def get_source_config(self, source_id: str) -> Optional[Dict]:
        """Get configuration for a specific source."""
        for source_config in self.config.get("sources", []):
            if source_config.get("id") == source_id:
                return source_config
        return None

