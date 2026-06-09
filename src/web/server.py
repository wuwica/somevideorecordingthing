"""FastAPI app factory and uvicorn daemon thread launcher."""
import asyncio
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.web.sse import sse_bus
from src.web.routers import recording, triggers, devices, preview, config

_STATIC_DIR = Path(__file__).parent.parent.parent / "web" / "static"
_OVERLAYS_DIR = Path(__file__).parent.parent.parent / "config" / "overlays"


def create_app(controller) -> FastAPI:
    app = FastAPI(title="Recording Admin", version="1.0")
    app.state.controller = controller
    controller.set_sse_bus(sse_bus)

    app.include_router(recording.router)
    app.include_router(triggers.router)
    app.include_router(devices.router)
    app.include_router(preview.router)
    app.include_router(config.router)

    @app.get("/events")
    async def sse_endpoint(request: Request):
        async def generate():
            async for chunk in sse_bus.subscribe():
                if await request.is_disconnected():
                    break
                yield chunk

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/status")
    def status(request: Request):
        return request.app.state.controller.get_status()

    if _STATIC_DIR.exists():
        @app.get("/")
        def admin_index():
            return FileResponse(_STATIC_DIR / "index.html")

        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    _OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/overlays", StaticFiles(directory=str(_OVERLAYS_DIR)), name="overlays")

    return app


def start_web_server(controller, host: str = "0.0.0.0", port: int = 8080) -> threading.Thread:
    """Start the web server in a daemon thread. Returns the thread."""
    app = create_app(controller)

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        sse_bus.set_loop(loop)
        controller._preview_manager.set_loop(loop)
        cfg = uvicorn.Config(
            app,
            host=host,
            port=port,
            loop="none",
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(cfg)
        loop.run_until_complete(server.serve())

    thread = threading.Thread(target=run, daemon=True, name="web-server")
    thread.start()
    log.info("Admin UI: http://%s:%d", host, port)
    return thread
