"""Read-only views over GitHub polling snapshots and per-project polling status."""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app import repositories as repos
from app.dependencies import get_db_conn

router = APIRouter(prefix="/api/v1/projects", tags=["github"])


def _require_project(conn: sqlite3.Connection, project_id: int) -> None:
    if not repos.project_get(conn, project_id):
        raise HTTPException(status_code=404, detail="Project not found")


@router.get("/{project_id}/github/issues")
def list_github_issues(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    _require_project(conn, project_id)
    return [
        {
            "issue_number": s.issue_number,
            "title": s.title,
            "state": s.state,
            "loom_status": s.loom_status,
            "updated_at": s.updated_at,
        }
        for s in repos.github_issue_list(conn, project_id)
    ]


@router.get("/{project_id}/github/pulls")
def list_github_pulls(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    _require_project(conn, project_id)
    return [
        {
            "pr_number": s.pr_number,
            "title": s.title,
            "state": s.state,
            "merged": s.merged,
            "updated_at": s.updated_at,
        }
        for s in repos.github_pr_list(conn, project_id)
    ]


@router.get("/{project_id}/github/polling-status")
def get_polling_status(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    _require_project(conn, project_id)
    status = repos.polling_status_get(conn, project_id)
    if not status:
        return {
            "project_id": project_id,
            "last_polled_at": None,
            "last_ok": False,
            "last_error": None,
        }
    return {
        "project_id": status.project_id,
        "last_polled_at": status.last_polled_at,
        "last_ok": status.last_ok,
        "last_error": status.last_error,
    }
