from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from app import repositories as repos
from app.db import Database
from app.github import (
    GitHubAdapter,
    GitHubAuthError,
    GitHubError,
    GitHubIssue,
    GitHubPullRequest,
    GitHubRateLimitError,
    RepoRef,
    parse_repo_url,
)

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 60


class PollingError(Exception):
    pass


@dataclass
class PollResult:
    issues: list[GitHubIssue]
    prs: list[GitHubPullRequest]


def _get_github_token(conn: sqlite3.Connection) -> str | None:
    return repos.setting_get(conn, "github_token") or None


@contextmanager
def _db_connection(database: Database) -> Any:
    conn = database.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class GitHubPoller:
    """Sync GitHub polling logic; scheduler runs it periodically."""

    def __init__(self, database: Database, adapter: GitHubAdapter | None = None) -> None:
        self.database = database
        self.adapter = adapter or GitHubAdapter()

    def _persist_issue(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        issue: GitHubIssue,
    ) -> None:
        existing = repos.github_issue_get(conn, project_id, issue.number)
        record = repos.github_issue_upsert(
            conn,
            project_id=project_id,
            issue_number=issue.number,
            title=issue.title,
            state=issue.state,
            is_open=issue.is_open,
            assignees=issue.assignees,
            labels=issue.labels,
            raw=issue.raw,
        )
        # Reopened issues return to the eligible pool.
        if existing is not None and not existing.is_open and record.is_open:
            repos.github_issue_set_loom_status(conn, project_id, issue.number, "unassigned")
            logger.info(
                "Reopened issue #%s on project %s marked unassigned",
                issue.number,
                project_id,
            )

    def _persist_pr(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        pr: GitHubPullRequest,
    ) -> None:
        repos.github_pr_upsert(
            conn,
            project_id=project_id,
            pr_number=pr.number,
            title=pr.title,
            state=pr.state,
            is_open=pr.is_open,
            is_draft=pr.is_draft,
            is_merged=pr.is_merged,
            merged_at=pr.merged_at,
            raw=pr.raw,
        )

    def _probe_closed_pr(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        ref: RepoRef,
        pr_number: int,
        token: str,
    ) -> None:
        try:
            pr = self.adapter.fetch_pr(ref, pr_number, token)
        except GitHubError:
            # If probe fails, mark closed without merge; downstream can re-probe.
            pr = GitHubPullRequest(
                number=pr_number,
                title="",
                state="closed",
                is_open=False,
                is_draft=False,
                is_merged=False,
                merged_at=None,
                raw={},
            )
        self._persist_pr(conn, project_id, pr)

    def _poll_project(self, conn: sqlite3.Connection, project: repos.Project) -> None:
        token = _get_github_token(conn)
        if not token:
            raise PollingError("GitHub token not configured")

        ref = parse_repo_url(project.repo_url)
        issues = self.adapter.list_open_issues(ref, token)
        prs = self.adapter.list_open_prs(ref, token)

        open_issue_numbers = {issue.number for issue in issues}
        open_pr_numbers = {pr.number for pr in prs}

        existing_issues = repos.github_issue_list(conn, project.id)
        existing_prs = repos.github_pr_list(conn, project.id)

        for issue in issues:
            self._persist_issue(conn, project.id, issue)

        # Issues that are no longer open are closed in our snapshot.
        for existing in existing_issues:
            if existing.issue_number not in open_issue_numbers:
                closed_issue = GitHubIssue(
                    number=existing.issue_number,
                    title=existing.title,
                    state="closed",
                    is_open=False,
                    assignees=existing.assignees,
                    labels=existing.labels,
                    raw=existing.raw,
                )
                self._persist_issue(conn, project.id, closed_issue)

        for pr in prs:
            self._persist_pr(conn, project.id, pr)

        # Probe PRs that closed since last poll to capture merged status.
        for existing_pr in existing_prs:
            if existing_pr.pr_number not in open_pr_numbers:
                self._probe_closed_pr(
                    conn, project.id, ref, existing_pr.pr_number, token
                )

        repos.github_polling_status_upsert(
            conn,
            project_id=project.id,
            success=True,
        )

    def poll_all(self) -> None:
        with _db_connection(self.database) as conn:
            projects = repos.project_list(conn)

        for project in projects:
            try:
                with _db_connection(self.database) as conn:
                    self._poll_project(conn, project)
            except (GitHubAuthError, GitHubRateLimitError, GitHubError, PollingError) as exc:
                logger.warning("GitHub polling failed for project %s: %s", project.id, exc)
                with _db_connection(self.database) as conn:
                    repos.github_polling_status_upsert(
                        conn,
                        project_id=project.id,
                        success=False,
                        error_message=str(exc),
                    )
            except Exception:
                logger.exception("Unexpected polling error for project %s", project.id)


class PollingScheduler:
    """Async scheduler that polls projects every minute."""

    def __init__(
        self,
        database: Database,
        adapter: GitHubAdapter | None = None,
        interval_seconds: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.poller = GitHubPoller(database, adapter)
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def _run_once(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.poller.poll_all)

    async def _loop(self) -> None:
        # Poll immediately on startup, then wait the interval.
        while not self._stop_event.is_set():
            await self._run_once()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.interval_seconds
                )
            except TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def poll_now(self) -> None:
        await self._run_once()
