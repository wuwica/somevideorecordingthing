"""SSE event bus: bridges non-asyncio threads into the FastAPI asyncio loop."""
import asyncio
import json
from typing import AsyncGenerator, Optional


class SSEBus:
    """Thread-safe publish/subscribe bus for Server-Sent Events.

    call `publish()` from any thread. Subscribers are asyncio queues living
    inside the web server's event loop, bridged via `call_soon_threadsafe`.
    """

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: list[asyncio.Queue] = []

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def publish(self, event_type: str, data: dict):
        """Thread-safe. May be called from Qt thread, GStreamer thread, etc."""
        if not self._loop or not self._loop.is_running():
            return
        payload = json.dumps({"type": event_type, "data": data})
        self._loop.call_soon_threadsafe(self._dispatch, payload)

    def _dispatch(self, payload: str):
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow client, drop event

    async def subscribe(self) -> AsyncGenerator[str, None]:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        try:
            while True:
                payload = await q.get()
                yield f"data: {payload}\n\n"
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)


sse_bus = SSEBus()
