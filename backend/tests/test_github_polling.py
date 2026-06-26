from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
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
from app.main import app
from app.polling import GitHubPoller
from app.repositories import (
    project_create,
    setting_set,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "github_polling_test.db"
    app.state.db.db_path = db_path
    get_db(db_path).reset()
    return TestClient(app)


@pytest.fixture
def db(client: TestClient) -> Any:
    return get_db(app.state.db.db_path)


@pytest.fixture
def project(db: Any) -> Any:
    with db.connect() as conn:
        project = project_create(conn, "demo", "https://github.com/brianlan/demo")
        conn.commit()
        return project


class FakeAdapter(GitHubAdapter):
    def __init__(
        self,
        issues: list[GitHubIssue] | None = None,
        prs: list[GitHubPullRequest] | None = None,
    ) -> None:
        super().__init__()
        self.issues = issues or []
        self.prs = prs or []
        self.closed_prs: dict[int, GitHubPullRequest] = {}
        self.calls: list[tuple[str, RepoRef, str]] = []

    def list_open_issues(self, ref: RepoRef, token: str) -> list[GitHubIssue]:
        self.calls.append(("issues", ref, token))
        return self.issues

    def list_open_prs(self, ref: RepoRef, token: str) -> list[GitHubPullRequest]:
        self.calls.append(("prs", ref, token))
        return self.prs

    def fetch_pr(self, ref: RepoRef, pr_number: int, token: str) -> GitHubPullRequest:
        self.calls.append(("fetch_pr", ref, token))
        return self.closed_prs.get(pr_number, GitHubPullRequest(
            number=pr_number,
            title="",
            state="closed",
            is_open=False,
            is_draft=False,
            is_merged=False,
            merged_at=None,
            raw={},
        ))


def _issue(number: int, title: str = "", state: str = "open") -> GitHubIssue:
    return GitHubIssue(
        number=number,
        title=title,
        state=state,
        is_open=state == "open",
        assignees=[],
        labels=[],
        raw={"number": number, "title": title, "state": state},
    )


def _pr(
    number: int,
    title: str = "",
    state: str = "open",
    merged: bool = False,
) -> GitHubPullRequest:
    return GitHubPullRequest(
        number=number,
        title=title,
        state=state,
        is_open=state == "open",
        is_draft=False,
        is_merged=merged,
        merged_at=None,
        raw={"number": number, "title": title, "state": state, "merged": merged},
    )


def test_parse_repo_url_variants() -> None:
    assert parse_repo_url("https://github.com/brianlan/demo") == RepoRef("brianlan", "demo")
    assert parse_repo_url("https://github.com/brianlan/demo.git") == RepoRef("brianlan", "demo")
    assert parse_repo_url("https://github.com/brianlan/demo/") == RepoRef("brianlan", "demo")
    assert parse_repo_url("http://github.com/brianlan/demo") == RepoRef("brianlan", "demo")


def test_parse_repo_url_rejects_invalid() -> None:
    with pytest.raises(GitHubError):
        parse_repo_url("https://gitlab.com/brianlan/demo")
    with pytest.raises(GitHubError):
        parse_repo_url("https://github.com/brianlan")
    with pytest.raises(GitHubError):
        parse_repo_url("ftp://github.com/brianlan/demo")
    with pytest.raises(GitHubError):
        parse_repo_url("https://github.com/brian lan/demo")


class MockAdapter(GitHubAdapter):
    def __init__(self, response: Any = None, status: int = 200) -> None:
        super().__init__()
        self.response = response
        self.status = status

    def _get(self, path: str, token: str) -> Any:
        request = httpx.Request("GET", "https://api.github.com" + path)
        response = httpx.Response(self.status, json=self.response, request=request)
        from app.github import _raise_for_status
        _raise_for_status(response)
        return response.json()


def test_adapter_list_open_issues_filters_pull_requests() -> None:
    adapter = MockAdapter(
        response=[
            {"number": 1, "title": "bug", "state": "open", "assignees": [], "labels": []},
            {
                "number": 2,
                "title": "feature",
                "state": "open",
                "assignees": [],
                "labels": [],
                "pull_request": {},
            },
        ]
    )
    issues = adapter.list_open_issues(RepoRef("brianlan", "demo"), "token")

    assert len(issues) == 1
    assert issues[0].number == 1


def test_adapter_raises_auth_error() -> None:
    adapter = MockAdapter(response={"message": "Bad credentials"}, status=401)
    with pytest.raises(GitHubAuthError):
        adapter.list_open_issues(RepoRef("brianlan", "demo"), "token")


def test_adapter_raises_rate_limit_error() -> None:
    adapter = MockAdapter(
        response={
            "message": "API rate limit exceeded",
            "documentation_url": "https://docs.github.com/rest/overview/rate-limits",
        },
        status=403,
    )
    with pytest.raises(GitHubRateLimitError):
        adapter.list_open_issues(RepoRef("brianlan", "demo"), "token")


def test_poll_persists_issues_and_prs(db: Any, project: Any) -> None:
    with db.connect() as conn:
        setting_set(conn, "github_token", "token")
        conn.commit()

    adapter = FakeAdapter(issues=[_issue(1, "bug")], prs=[_pr(2, "feature")])
    poller = GitHubPoller(db, adapter)
    poller.poll_all()

    with db.connect() as conn:
        issues = db_execute_list(
            conn,
            "SELECT issue_number, title, is_open FROM github_issues WHERE project_id = ?",
            (project.id,),
        )
        prs = db_execute_list(
            conn,
            "SELECT pr_number, title, is_open FROM github_pull_requests WHERE project_id = ?",
            (project.id,),
        )
        status = conn.execute(
            "SELECT last_error_message FROM github_polling_status WHERE project_id = ?",
            (project.id,),
        ).fetchone()

    assert issues == [(1, "bug", 1)]
    assert prs == [(2, "feature", 1)]
    assert status["last_error_message"] is None


def test_poll_reopened_issue_resets_loom_status(db: Any, project: Any) -> None:
    with db.connect() as conn:
        setting_set(conn, "github_token", "token")
        conn.commit()

    adapter = FakeAdapter(issues=[_issue(1, "bug", state="closed")])
    poller = GitHubPoller(db, adapter)
    poller.poll_all()

    with db.connect() as conn:
        issue = conn.execute(
            "SELECT loom_status, is_open FROM github_issues WHERE project_id = ?",
            (project.id,),
        ).fetchone()
    assert issue["is_open"] == 0
    assert issue["loom_status"] == "unassigned"

    adapter.issues = [_issue(1, "bug", state="open")]
    poller.poll_all()

    with db.connect() as conn:
        issue = conn.execute(
            "SELECT loom_status, is_open FROM github_issues WHERE project_id = ?",
            (project.id,),
        ).fetchone()
    assert issue["is_open"] == 1
    assert issue["loom_status"] == "unassigned"


def test_poll_closed_issue_snapshot(db: Any, project: Any) -> None:
    with db.connect() as conn:
        setting_set(conn, "github_token", "token")
        conn.commit()

    adapter = FakeAdapter(issues=[_issue(1, "bug")])
    poller = GitHubPoller(db, adapter)
    poller.poll_all()

    adapter.issues = []
    poller.poll_all()

    with db.connect() as conn:
        issue = conn.execute(
            "SELECT state, is_open FROM github_issues WHERE project_id = ?",
            (project.id,),
        ).fetchone()
    assert issue["state"] == "closed"
    assert issue["is_open"] == 0


def test_poll_probes_closed_pr_for_merge_status(db: Any, project: Any) -> None:
    with db.connect() as conn:
        setting_set(conn, "github_token", "token")
        conn.commit()

    adapter = FakeAdapter(prs=[_pr(2, "feature")])
    adapter.closed_prs[2] = _pr(2, "feature", state="closed", merged=True)
    poller = GitHubPoller(db, adapter)
    poller.poll_all()

    adapter.prs = []
    poller.poll_all()

    with db.connect() as conn:
        pr = conn.execute(
            "SELECT state, is_open, is_merged FROM github_pull_requests WHERE project_id = ?",
            (project.id,),
        ).fetchone()
    assert pr["state"] == "closed"
    assert pr["is_open"] == 0
    assert pr["is_merged"] == 1


def test_poll_surfaces_token_error(db: Any, project: Any) -> None:
    with db.connect() as conn:
        setting_set(conn, "github_token", "token")
        conn.commit()

    class FailingAdapter(GitHubAdapter):
        def list_open_issues(self, ref: RepoRef, token: str) -> list[GitHubIssue]:
            raise GitHubAuthError("token revoked")

    poller = GitHubPoller(db, FailingAdapter())
    poller.poll_all()

    with db.connect() as conn:
        status = conn.execute(
            "SELECT last_error_message FROM github_polling_status WHERE project_id = ?",
            (project.id,),
        ).fetchone()
    assert "token revoked" in status["last_error_message"]


def test_api_list_issues_and_prs(client: TestClient, project: Any) -> None:
    with get_db(app.state.db.db_path).connect() as conn:
        setting_set(conn, "github_token", "token")
        conn.execute(
            """
            INSERT INTO github_issues (
                project_id, issue_number, title, state, is_open,
                assignees_json, labels_json, raw_json, loom_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project.id, 1, "bug", "open", 1, "[]", "[]", "{}", "unassigned"),
        )
        conn.execute(
            """
            INSERT INTO github_pull_requests
            (project_id, pr_number, title, state, is_open, is_draft, is_merged, merged_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project.id, 2, "feature", "open", 1, 0, 0, None, "{}"),
        )
        conn.commit()

    issues_resp = client.get(f"/api/v1/projects/{project.id}/github/issues")
    assert issues_resp.status_code == 200
    issues = issues_resp.json()
    assert len(issues) == 1
    assert issues[0]["issue_number"] == 1

    prs_resp = client.get(f"/api/v1/projects/{project.id}/github/pull-requests")
    assert prs_resp.status_code == 200
    prs = prs_resp.json()
    assert len(prs) == 1
    assert prs[0]["pr_number"] == 2


def test_api_polling_status(client: TestClient, project: Any) -> None:
    with get_db(app.state.db.db_path).connect() as conn:
        conn.execute(
            """
            INSERT INTO github_polling_status
            (project_id, last_success_at, last_error_at, last_error_message)
            VALUES (?, CURRENT_TIMESTAMP, NULL, NULL)
            """,
            (project.id,),
        )
        conn.commit()

    resp = client.get(f"/api/v1/projects/{project.id}/github/polling-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project.id
    assert data["last_error_message"] is None


def test_api_polling_status_missing_project(client: TestClient) -> None:
    resp = client.get("/api/v1/projects/999/github/polling-status")
    assert resp.status_code == 404


def db_execute_list(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in conn.execute(sql, params).fetchall()]
