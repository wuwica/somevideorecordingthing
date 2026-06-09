"""Live preview streaming endpoints."""
import asyncio

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

router = APIRouter(prefix="/api/preview", tags=["preview"])

_BOUNDARY = b"--frame\r\nContent-Type: image/jpeg\r\n"
_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}
_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/status")
def preview_status(request: Request):
    return request.app.state.controller.get_preview_status()


@router.get("/sources")
def preview_sources(request: Request):
    return {"sources": request.app.state.controller.list_preview_sources()}


@router.get("/frame")
def preview_frame(request: Request):
    """Latest composited preview frame as JPEG (for one-shot fetch)."""
    jpeg = request.app.state.controller.get_preview_jpeg()
    if not jpeg:
        return Response(status_code=503, content="No preview available")
    return Response(content=jpeg, media_type="image/jpeg", headers=_NO_CACHE)


@router.get("/source/{source_id}/frame")
def preview_source_frame(source_id: str, request: Request):
    """Latest frame for a single configured source (for one-shot fetch)."""
    jpeg = request.app.state.controller.get_source_preview_jpeg(source_id)
    if not jpeg:
        return Response(status_code=503, content=f"No preview for source '{source_id}'")
    return Response(content=jpeg, media_type="image/jpeg", headers=_NO_CACHE)


async def _ws_stream(websocket: WebSocket, key: str):
    """Shared WebSocket streaming logic: push binary JPEG frames, drop if slow."""
    pm = websocket.app.state.controller._preview_manager
    await websocket.accept()
    # Send latest cached frame immediately so new clients don't stall waiting for the next tick.
    latest = pm.get_latest_jpeg() if key == "__composite__" else pm.get_source_jpeg(key)
    if latest:
        try:
            await websocket.send_bytes(latest)
        except Exception:
            return
    q = pm.subscribe(key)
    try:
        while True:
            try:
                jpeg = await asyncio.wait_for(q.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            try:
                await websocket.send_bytes(jpeg)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        pm.unsubscribe(q, key)


@router.websocket("/ws")
async def preview_ws(websocket: WebSocket):
    """WebSocket stream of the live composited preview (binary JPEG frames)."""
    await _ws_stream(websocket, "__composite__")


@router.websocket("/source/{source_id}/ws")
async def preview_source_ws(source_id: str, websocket: WebSocket):
    """WebSocket stream for a single source (binary JPEG frames)."""
    await _ws_stream(websocket, source_id)


@router.get("/mjpeg")
async def preview_mjpeg(request: Request):
    """MJPEG fallback stream of the live composited preview."""
    pm = request.app.state.controller._preview_manager
    q = pm.subscribe("__composite__")

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    jpeg = await asyncio.wait_for(q.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                yield (
                    _BOUNDARY
                    + b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
        finally:
            pm.unsubscribe(q, "__composite__")

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers=_STREAM_HEADERS,
    )


@router.get("/source/{source_id}/mjpeg")
async def preview_source_mjpeg(source_id: str, request: Request):
    """MJPEG fallback stream for a single source."""
    pm = request.app.state.controller._preview_manager
    q = pm.subscribe(source_id)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    jpeg = await asyncio.wait_for(q.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                yield (
                    _BOUNDARY
                    + b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
        finally:
            pm.unsubscribe(q, source_id)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers=_STREAM_HEADERS,
    )
