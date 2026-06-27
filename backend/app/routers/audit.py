"""Browsable audit trail API routes (OBS-1, OBS-4).

- ``GET /api/v1/audit`` — all audit events.
- ``GET /api/v1/projects/{project_id}/audit`` — per-project audit events.
- ``GET /api/v1/agents/{agent_instance_id}/audit`` — per-agent audit events.
"""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app import repositories as repos
from app.dependencies import get_db_conn

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit")
def list_audit(
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    return repos.audit_event_list(conn)


@router.get("/projects/{project_id}/audit")
def list_project_audit(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    if not repos.project_get(conn, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return repos.audit_event_list(conn, project_id=project_id)


@router.get("/agents/{agent_instance_id}/audit")
def list_agent_audit(
    agent_instance_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    if repos.agent_instance_get(conn, agent_instance_id) is None:
        raise HTTPException(status_code=404, detail="Agent instance not found")
    return repos.audit_event_list(conn, agent_instance_id=agent_instance_id)
