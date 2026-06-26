"""Console replay and live stream API routes (T14).

- ``GET /api/v1/agents/{id}/console/history`` — return all stored chunks (FR-34).
- ``GET /api/v1/agents/{id}/console/stream`` — SSE: replay history, then live (FR-34).
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app import repositories as repos
from app.console import ConsoleBroker, history_then_live
from app.db import get_db
from app.dependencies import get_db_conn

router = APIRouter(prefix="/api/v1/agents/{agent_instance_id}/console", tags=["console"])


def _get_broker(request: Request) -> ConsoleBroker | None:
    return getattr(request.app.state, "console_broker", None)


@router.get("/history")
def console_history(
    agent_instance_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[dict[str, object]]:
    """FR-34: Return all stored console chunks for replay."""
    return repos.console_chunk_list(conn, agent_instance_id)


@router.get("/stream")
async def console_stream(
    agent_instance_id: int,
    request: Request,
) -> StreamingResponse:
    """FR-34: SSE stream — replay history first, then live chunks via broker."""
    broker = _get_broker(request)
    db_path = request.app.state.db.db_path

    # Read history before entering the async generator (avoids holding a conn open).
    db = get_db(db_path)
    with db.connect() as conn:
        chunks = repos.console_chunk_list(conn, agent_instance_id)

    async def generate() -> AsyncGenerator[str, None]:
        async for event in history_then_live(
            broker=broker,
            chunks=chunks,
            agent_instance_id=agent_instance_id,
            is_disconnected=request.is_disconnected,
        ):
            yield event

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
