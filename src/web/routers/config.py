"""Layout / source / overlay configuration endpoints."""
from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/config", tags=["config"])


class SourcePatch(BaseModel):
    id: str
    position: Optional[dict] = None
    size: Optional[dict] = None
    z_order: Optional[int] = None
    fps: Optional[int] = None
    rotation: Optional[int] = None       # 0, 90, 180, 270
    mask_shape: Optional[str] = None     # "rect" | "circle" | "polygon"
    mask_points: Optional[list] = None   # [[x,y],...] normalized 0-1 (polygon only)
    mask_position: Optional[dict] = None # {x, y} top-left of mask in source frame pixels
    mask_size: Optional[dict] = None     # {width, height} of mask in source frame pixels


class OutputPatch(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None


class ConfigUpdate(BaseModel):
    sources: Optional[list[SourcePatch]] = None
    output: Optional[OutputPatch] = None


class OverlayPatch(BaseModel):
    position: Optional[dict] = None
    size: Optional[dict] = None
    opacity: Optional[float] = None
    z_order: Optional[int] = None


@router.get("")
def get_config(request: Request):
    return request.app.state.controller.get_config()


@router.post("")
def update_config(body: ConfigUpdate, request: Request):
    sources = [s.model_dump(exclude_none=True) for s in body.sources] if body.sources else None
    output = body.output.model_dump(exclude_none=True) if body.output else None
    ok, msg = request.app.state.controller.update_config(sources, output)
    if not ok:
        return JSONResponse(status_code=409, content={"detail": msg})
    return {"ok": True}


@router.post("/overlays")
async def upload_overlay(
    request: Request,
    file: UploadFile = File(...),
    x: int = Form(0),
    y: int = Form(0),
    width: int = Form(200),
    height: int = Form(200),
    opacity: float = Form(1.0),
):
    data = await file.read()
    ok, msg = request.app.state.controller.upload_overlay(
        file.filename,
        data,
        position={"x": x, "y": y},
        size={"width": width, "height": height},
        opacity=opacity,
    )
    if not ok:
        return JSONResponse(status_code=409, content={"detail": msg})
    return {"ok": True, "path": msg}


@router.patch("/overlays/{index}")
def update_overlay(index: int, body: OverlayPatch, request: Request):
    patch = body.model_dump(exclude_none=True)
    ok, msg = request.app.state.controller.update_overlay(index, patch)
    if not ok:
        return JSONResponse(status_code=409, content={"detail": msg})
    return {"ok": True}


@router.delete("/overlays/{index}")
def delete_overlay(index: int, request: Request):
    ok, msg = request.app.state.controller.remove_overlay(index)
    if not ok:
        return JSONResponse(status_code=409, content={"detail": msg})
    return {"ok": True}
