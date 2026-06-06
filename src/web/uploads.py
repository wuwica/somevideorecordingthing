"""File upload utilities for trigger reference files."""
import os
from pathlib import Path


def save_upload(upload_dir: str, filename: str, content: bytes) -> str:
    """Write uploaded bytes to disk and return the absolute path."""
    dest = Path(upload_dir) / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return str(dest.resolve())
