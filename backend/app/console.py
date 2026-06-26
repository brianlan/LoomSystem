"""Console capture, replay, and live stream transport (T14).

Persists per-agent console output from trigger execution, exposes history
replay, and streams new chunks to connected clients via Server-Sent Events
(SSE). The in-memory broker bridges sync trigger execution to async SSE
subscribers (NFR-1: ≤2s latency).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable


class ConsoleBroker:
    """In-memory pub/sub for live console streaming.

    Subscribers create an ``asyncio.Queue`` via :meth:`subscribe`. The trigger
    service calls :meth:`publish` for each output line; connected SSE endpoints
    receive them in real time.

    Thread-safe: ``publish`` can be called from any thread. If the event loop
    is running (production with uvicorn), items are scheduled via
    ``call_soon_threadsafe``. Otherwise (tests), items are put directly.
    """

    def __init__(self) -> None:
        self._subs: dict[int, list[asyncio.Queue[str | None]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, agent_instance_id: int) -> asyncio.Queue[str | None]:
        q: asyncio.Queue[str | None] = asyncio.Queue()
        self._subs.setdefault(agent_instance_id, []).append(q)
        return q

    def unsubscribe(self, agent_instance_id: int, q: asyncio.Queue[str | None]) -> None:
        subs = self._subs.get(agent_instance_id)
        if subs:
            try:
                subs.remove(q)
            except ValueError:
                pass

    def publish(self, agent_instance_id: int, chunk: str) -> None:
        subs = self._subs.get(agent_instance_id, [])
        if not subs:
            return
        if self._loop and self._loop.is_running():
            for q in subs:
                self._loop.call_soon_threadsafe(q.put_nowait, chunk)
        else:
            for q in subs:
                q.put_nowait(chunk)


def sse_event(data: dict[str, object]) -> str:
    """Format one Server-Sent Event."""
    return f"data: {json.dumps(data)}\n\n"


async def history_then_live(
    broker: ConsoleBroker | None,
    chunks: list[dict[str, object]],
    agent_instance_id: int,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    poll_timeout: float = 2.0,
) -> AsyncGenerator[str, None]:
    """Yield SSE events: replay history, then stream live chunks via broker.

    ``is_disconnected`` is an awaitable callable (e.g. ``request.is_disconnected``)
    used to detect client disconnect during live streaming. If ``None``, live
    streaming runs until the broker sends a ``None`` sentinel.
    """
    last_index = -1
    for chunk in chunks:
        yield sse_event(chunk)
        ci = chunk.get("chunk_index")
        if isinstance(ci, int) and ci > last_index:
            last_index = ci

    if broker is None:
        return

    q = broker.subscribe(agent_instance_id)
    try:
        while True:
            if is_disconnected is not None and await is_disconnected():
                break
            try:
                item = await asyncio.wait_for(q.get(), timeout=poll_timeout)
            except asyncio.TimeoutError:
                continue
            if item is None:
                break
            last_index += 1
            yield sse_event({"chunk_index": last_index, "content": item})
    finally:
        broker.unsubscribe(agent_instance_id, q)
