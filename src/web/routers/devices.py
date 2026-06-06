"""Device enumeration endpoints."""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _ctrl(request: Request):
    return request.app.state.controller


@router.get("")
def get_devices(request: Request):
    return _ctrl(request).get_devices()


@router.get("/usb")
def get_usb_devices(request: Request):
    return _ctrl(request).usb_monitor.get_mounted_devices()


@router.post("/refresh")
def refresh_devices(request: Request):
    return _ctrl(request).refresh_devices()
