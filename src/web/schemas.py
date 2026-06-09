"""Pydantic request/response models for the admin API."""
from pydantic import BaseModel
from typing import Optional


class RecordingStatusResponse(BaseModel):
    is_recording: bool
    output_path: Optional[str]
    usb_mount_point: Optional[str]
    usb_devices: dict
    active_trigger: Optional[str]
    trigger: dict


class ActionResponse(BaseModel):
    ok: bool
    message: str
