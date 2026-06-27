"""In-app notification feed API routes (FR-35).

- ``GET /api/v1/notifications`` — all notifications (optional ``unread_only``).
- ``GET /api/v1/projects/{project_id}/notifications`` — per-project feed.
- ``POST /api/v1/notifications/{notification_id}/read`` — mark one read.
"""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories as repos
from app.dependencies import get_db_conn
from app.schemas import MessageResponse

router = APIRouter(prefix="/api/v1", tags=["notifications"])


@router.get("/notifications")
def list_notifications(
    unread_only: bool = Query(default=False),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    return repos.notification_list(conn, unread_only=unread_only)


@router.get("/projects/{project_id}/notifications")
def list_project_notifications(
    project_id: int,
    unread_only: bool = Query(default=False),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    if not repos.project_get(conn, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return repos.notification_list(conn, project_id=project_id, unread_only=unread_only)


@router.post("/notifications/{notification_id}/read", response_model=MessageResponse)
def mark_notification_read(
    notification_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    if not repos.notification_exists(conn, notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    repos.notification_mark_read(conn, notification_id)
    return {"message": f"Notification {notification_id} marked read"}
