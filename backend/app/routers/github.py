import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app import repositories as repos
from app import schemas
from app.dependencies import get_db_conn

router = APIRouter(prefix="/api/v1/projects/{project_id}/github", tags=["github"])


def _ensure_project(conn: sqlite3.Connection, project_id: int) -> repos.Project:
    project = repos.project_get(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/issues", response_model=list[schemas.GitHubIssueRead])
def list_issues(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[repos.GitHubIssueRecord]:
    _ensure_project(conn, project_id)
    return repos.github_issue_list(conn, project_id)


@router.get("/pull-requests", response_model=list[schemas.GitHubPullRequestRead])
def list_prs(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[repos.GitHubPullRequestRecord]:
    _ensure_project(conn, project_id)
    return repos.github_pr_list(conn, project_id)


@router.get("/polling-status", response_model=schemas.GitHubPollingStatusRead)
def get_polling_status(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.GitHubPollingStatus:
    _ensure_project(conn, project_id)
    status = repos.github_polling_status_get(conn, project_id)
    if not status:
        raise HTTPException(status_code=404, detail="Polling status not available yet")
    return status
