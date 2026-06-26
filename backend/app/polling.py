"""Per-project GitHub polling service.

Polls each project's bound repo once per minute, persists issue/PR snapshots,
and records polling status so the UI can surface token failures clearly (EC-5).
Reopened issues re-enter the eligible (unassigned) pool (D45, EC-11).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from app import repositories as repos
from app.db import get_db
from app.github import (
    GitHubAdapter,
    GitHubError,
    HttpGitHubAdapter,
    IssueDTO,
    PullRequestDTO,
    parse_repo_url,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60


def _sync_issue_snapshots(
    conn: sqlite3.Connection,
    project_id: int,
    open_issues: list[IssueDTO],
) -> None:
    """Upsert open issues; mark disappeared issues resolved, reopened issues unassigned."""
    seen_numbers = {issue.number for issue in open_issues}
    for issue in open_issues:
        existing = repos.github_issue_get(conn, project_id, issue.number)
        if existing is None:
            # New open issue -> enters the eligible pool unassigned.
            conn.execute(
                "INSERT INTO github_issues (project_id, issue_number, title, state, loom_status) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, issue.number, issue.title, issue.state, repos.ISSUE_STATUS_UNASSIGNED),
            )
        else:
            # Existing issue. If it was resolved and is now open again, it was reopened (D45).
            loom_status = existing.loom_status
            if loom_status == repos.ISSUE_STATUS_RESOLVED:
                loom_status = repos.ISSUE_STATUS_UNASSIGNED
            conn.execute(
                "UPDATE github_issues SET title = ?, state = ?, loom_status = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE project_id = ? AND issue_number = ?",
                (issue.title, issue.state, loom_status, project_id, issue.number),
            )
    # Issues that disappeared from the open list are closed upstream -> resolved.
    if seen_numbers:
        conn.execute(
            "UPDATE github_issues SET loom_status = ?, state = 'closed', "
            "updated_at = CURRENT_TIMESTAMP WHERE project_id = ? "
            "AND issue_number NOT IN (%s)" % ",".join("?" * len(seen_numbers)),
            [repos.ISSUE_STATUS_RESOLVED, project_id, *seen_numbers],
        )
    else:
        conn.execute(
            "UPDATE github_issues SET loom_status = ?, state = 'closed', "
            "updated_at = CURRENT_TIMESTAMP WHERE project_id = ?",
            (repos.ISSUE_STATUS_RESOLVED, project_id),
        )


def _sync_pr_snapshots(
    conn: sqlite3.Connection,
    project_id: int,
    open_prs: list[PullRequestDTO],
) -> None:
    seen_numbers = {pr.number for pr in open_prs}
    for pr in open_prs:
        existing = repos.github_pr_get(conn, project_id, pr.number)
        if existing is None:
            conn.execute(
                "INSERT INTO github_prs (project_id, pr_number, title, state, merged) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, pr.number, pr.title, pr.state, int(pr.merged)),
            )
        else:
            conn.execute(
                "UPDATE github_prs SET title = ?, state = ?, merged = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE project_id = ? AND pr_number = ?",
                (pr.title, pr.state, int(pr.merged), project_id, pr.number),
            )
    if seen_numbers:
        conn.execute(
            "DELETE FROM github_prs WHERE project_id = ? AND pr_number NOT IN (%s)"
            % ",".join("?" * len(seen_numbers)),
            [project_id, *seen_numbers],
        )
    else:
        conn.execute("DELETE FROM github_prs WHERE project_id = ?", (project_id,))


def poll_project(
    conn: sqlite3.Connection,
    project: repos.Project,
    adapter: GitHubAdapter,
) -> tuple[bool, str | None]:
    """Fetch and persist issue/PR snapshots for one project. Returns (ok, error)."""
    try:
        owner, repo = parse_repo_url(project.repo_url)
        issues = adapter.list_open_issues(owner, repo)
        prs = adapter.list_open_pull_requests(owner, repo)
    except GitHubError as exc:
        _record_status(conn, project.id, ok=False, error=str(exc))
        return (False, str(exc))

    _sync_issue_snapshots(conn, project.id, issues)
    _sync_pr_snapshots(conn, project.id, prs)
    _record_status(conn, project.id, ok=True, error=None)
    return (True, None)


def _record_status(
    conn: sqlite3.Connection, project_id: int, ok: bool, error: str | None
) -> None:
    conn.execute(
        """
        INSERT INTO polling_status (project_id, last_polled_at, last_ok, last_error)
        VALUES (?, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            last_polled_at = excluded.last_polled_at,
            last_ok = excluded.last_ok,
            last_error = excluded.last_error
        """,
        (project_id, int(ok), error),
    )


def poll_all(conn: sqlite3.Connection, adapter: GitHubAdapter) -> None:
    """Poll every project once using a shared adapter (token is global, D36)."""
    projects = repos.project_list(conn)
    for project in projects:
        try:
            poll_project(conn, project, adapter)
            conn.commit()
        except Exception:  # pragma: no cover - defensive: never let one project kill the loop
            conn.rollback()
            logger.exception("Polling failed for project %s", project.id)


# A factory builds one adapter per poll cycle from app-level settings.
AdapterFactory = Callable[[], GitHubAdapter]


def make_adapter_factory(db_path: Path | str | None) -> AdapterFactory:
    """Return a factory that reads the app-level token/proxy from the given DB path."""

    def factory() -> GitHubAdapter:
        db = get_db(db_path)
        with db.connect() as conn:
            token = repos.setting_get(conn, "github_token") or ""
            proxy = repos.setting_get(conn, "https_proxy") or None
        return HttpGitHubAdapter(token=token, proxy_url=proxy)

    return factory


class PollingService:
    """Background loop that polls all projects every POLL_INTERVAL_SECONDS."""

    def __init__(
        self, db_path: Path | str | None, adapter_factory: AdapterFactory
    ) -> None:
        self._db_path = db_path
        self._adapter_factory = adapter_factory
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            try:
                adapter = self._adapter_factory()
                db = get_db(self._db_path)
                with db.connect() as conn:
                    poll_all(conn, adapter)
            except Exception:  # pragma: no cover
                logger.exception("Polling loop iteration failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
