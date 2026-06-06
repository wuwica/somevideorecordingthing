"""Shared application state dataclass."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AppState:
    is_recording: bool = False
    output_path: Optional[str] = None
    usb_mount_point: Optional[str] = None
    upload_dir: str = "config/uploads"
