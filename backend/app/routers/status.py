"""Top-level aggregate dashboard status API (OBS-5).

- ``GET /api/v1/status`` — running counts, recent failures, backlog size.
"""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from app import repositories as repos
from app.dependencies import get_db_conn

router = APIRouter(prefix="/api/v1", tags=["status"])


@router.get("/status")
def aggregate_status(
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    return repos.aggregate_status(conn)
