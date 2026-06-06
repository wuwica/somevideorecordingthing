"""Recording control endpoints."""
from fastapi import APIRouter, Request, HTTPException
from src.web.schemas import RecordingStatusResponse, ActionResponse

router = APIRouter(prefix="/api/recording", tags=["recording"])


def _ctrl(request: Request):
    return request.app.state.controller


@router.get("/status", response_model=RecordingStatusResponse)
def get_status(request: Request):
    return _ctrl(request).get_status()


@router.post("/start", response_model=ActionResponse)
def start_recording(request: Request):
    ok, msg = _ctrl(request).start_recording()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "message": msg}


@router.post("/stop", response_model=ActionResponse)
def stop_recording(request: Request):
    ok, msg = _ctrl(request).stop_recording()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "message": msg}
