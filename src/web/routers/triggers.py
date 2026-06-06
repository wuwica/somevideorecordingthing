"""Trigger configuration endpoints."""
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from src.web.schemas import TriggerAudioConfig, TriggerFrameConfig, ActionResponse
from src.web.uploads import save_upload
from src.web.sse import sse_bus

router = APIRouter(prefix="/api/triggers", tags=["triggers"])


def _ctrl(request: Request):
    return request.app.state.controller


@router.get("")
def get_triggers(request: Request):
    return _ctrl(request).trigger_manager.status_dict()


# ------------------------------------------------------------------ audio

@router.post("/audio", response_model=ActionResponse)
async def upload_audio_trigger(
    request: Request,
    file: UploadFile = File(...),
    threshold: float = Form(0.88),
    check_interval: float = Form(0.5),
):
    content = await file.read()
    ctrl = _ctrl(request)
    path = save_upload(ctrl.state.upload_dir, f"audio_ref_{file.filename}", content)
    ok, msg = ctrl.reload_audio_trigger(path, threshold, check_interval)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": f"Audio trigger updated: {path}"}


@router.post("/audio/config", response_model=ActionResponse)
def update_audio_config(request: Request, body: TriggerAudioConfig):
    ctrl = _ctrl(request)
    tm = ctrl.trigger_manager
    if not tm.audio_ready:
        raise HTTPException(status_code=404, detail="No audio trigger configured")
    ok, msg = ctrl.reload_audio_trigger(
        tm.audio_trigger.reference_clip_path,
        body.threshold,
        body.check_interval,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": "Audio trigger config updated"}


@router.delete("/audio", response_model=ActionResponse)
def disable_audio_trigger(request: Request):
    ok, msg = _ctrl(request).disable_audio_trigger()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": "Audio trigger disabled"}


# ------------------------------------------------------------------ frame

@router.post("/frame", response_model=ActionResponse)
async def upload_frame_trigger(
    request: Request,
    file: UploadFile = File(...),
    threshold: float = Form(0.85),
    check_interval: float = Form(1.0),
):
    content = await file.read()
    ctrl = _ctrl(request)
    path = save_upload(ctrl.state.upload_dir, f"frame_ref_{file.filename}", content)
    ok, msg = ctrl.reload_frame_trigger(path, threshold, check_interval)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": f"Frame trigger updated: {path}"}


@router.post("/frame/config", response_model=ActionResponse)
def update_frame_config(request: Request, body: TriggerFrameConfig):
    ctrl = _ctrl(request)
    tm = ctrl.trigger_manager
    if not tm.frame_ready:
        raise HTTPException(status_code=404, detail="No frame trigger configured")
    ok, msg = ctrl.reload_frame_trigger(
        tm.frame_detector.reference_frame_path,
        body.threshold,
        body.check_interval,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": "Frame trigger config updated"}


@router.delete("/frame", response_model=ActionResponse)
def disable_frame_trigger(request: Request):
    ok, msg = _ctrl(request).disable_frame_trigger()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": "Frame trigger disabled"}
