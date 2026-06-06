"""Pydantic request/response models for the admin API."""
from pydantic import BaseModel, Field
from typing import Optional


class TriggerAudioConfig(BaseModel):
    threshold: float = Field(0.88, ge=0.0, le=1.0)
    check_interval: float = Field(0.5, gt=0.0)


class TriggerFrameConfig(BaseModel):
    threshold: float = Field(0.85, ge=0.0, le=1.0)
    check_interval: float = Field(1.0, gt=0.0)


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
