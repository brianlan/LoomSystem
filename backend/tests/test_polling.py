import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import repositories as repos
from app.db import get_db
from app.github import GitHubError, IssueDTO, PullRequestDTO
from app.main import app
from app.polling import poll_all, poll_project


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "polling_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def client(db_path: Path) -> TestClient:
    app.state.db.db_path = db_path
    return TestClient(app)


def _make_project(conn: sqlite3.Connection, name: str = "demo") -> repos.Project:
    return repos.project_create(conn, name, "git@github.com:brianlan/demo.git")


class _FakeAdapter:
    def __init__(
        self,
        issues: list[IssueDTO] | None = None,
        prs: list[PullRequestDTO] | None = None,
        error: GitHubError | None = None,
    ) -> None:
        self._issues = issues or []
        self._prs = prs or []
        self._error = error

    def list_open_issues(self, owner: str, repo: str) -> list[IssueDTO]:
        if self._error:
            raise self._error
        return self._issues

    def list_open_pull_requests(self, owner: str, repo: str) -> list[PullRequestDTO]:
        if self._error:
            raise self._error
        return self._prs

    def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequestDTO:
        raise NotImplementedError

    def get_issue(self, owner: str, repo: str, number: int) -> IssueDTO:
        raise NotImplementedError

    def update_pr(self, owner: str, repo: str, number: int, body: str) -> None:
        raise NotImplementedError


def test_poll_persists_issues_and_prs(conn: sqlite3.Connection) -> None:
    project = _make_project(conn)
    adapter = _FakeAdapter(
        issues=[IssueDTO(number=1, title="First", state="open"),
                IssueDTO(number=3, title="Third", state="open")],
        prs=[PullRequestDTO(number=2, title="PR", state="open", merged=False)],
    )
    ok, error = poll_project(conn, project, adapter)

    assert ok is True
    assert error is None
    issues = repos.github_issue_list(conn, project.id)
    assert {i.issue_number for i in issues} == {1, 3}
    assert all(i.loom_status == repos.ISSUE_STATUS_UNASSIGNED for i in issues)
    prs = repos.github_pr_list(conn, project.id)
    assert [p.pr_number for p in prs] == [2]
    status = repos.polling_status_get(conn, project.id)
    assert status is not None and status.last_ok is True


def test_poll_surfaces_token_failure(conn: sqlite3.Connection) -> None:
    project = _make_project(conn)
    adapter = _FakeAdapter(error=GitHubError("bad token", kind="invalid_token"))
    ok, error = poll_project(conn, project, adapter)

    assert ok is False
    assert error is not None and "bad token" in error
    status = repos.polling_status_get(conn, project.id)
    assert status is not None
    assert status.last_ok is False
    assert status.last_error is not None and "bad token" in status.last_error
    # No snapshots written on failure.
    assert repos.github_issue_list(conn, project.id) == []


def test_disappeared_issue_marked_resolved(conn: sqlite3.Connection) -> None:
    project = _make_project(conn)
    poll_project(conn, project, _FakeAdapter(issues=[IssueDTO(1, "One", "open")]))

    # Next poll: issue 1 is gone (closed upstream).
    poll_project(conn, project, _FakeAdapter(issues=[]))

    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_RESOLVED
    assert issue.state == "closed"


def test_reopened_issue_reenters_unassigned_pool(conn: sqlite3.Connection) -> None:
    project = _make_project(conn)
    # Issue appears, then disappears (resolved).
    poll_project(conn, project, _FakeAdapter(issues=[IssueDTO(1, "One", "open")]))
    poll_project(conn, project, _FakeAdapter(issues=[]))
    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_RESOLVED

    # Issue reappears as open -> reopened (D45, EC-11).
    poll_project(conn, project, _FakeAdapter(issues=[IssueDTO(1, "One", "open")]))
    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_UNASSIGNED


def test_existing_in_progress_status_preserved_on_repoll(conn: sqlite3.Connection) -> None:
    project = _make_project(conn)
    poll_project(conn, project, _FakeAdapter(issues=[IssueDTO(1, "One", "open")]))
    # Downstream orchestrator advances the issue to in-progress.
    repos.github_issue_set_status(conn, project.id, 1, repos.ISSUE_STATUS_IN_PROGRESS)
    conn.commit()

    poll_project(conn, project, _FakeAdapter(issues=[IssueDTO(1, "One updated", "open")]))
    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_IN_PROGRESS
    assert issue.title == "One updated"


def test_pr_closed_without_merge_probe_signal(conn: sqlite3.Connection) -> None:
    project = _make_project(conn)
    poll_project(conn, project, _FakeAdapter(prs=[PullRequestDTO(7, "PR", "open", False)]))
    pr = repos.github_pr_get(conn, project.id, 7)
    assert pr is not None
    assert pr.merged is False

    # Open PR disappears from the open list -> deleted from snapshot.
    poll_project(conn, project, _FakeAdapter(prs=[]))
    assert repos.github_pr_get(conn, project.id, 7) is None


def test_poll_all_iterates_all_projects(conn: sqlite3.Connection) -> None:
    p1 = repos.project_create(conn, "a", "git@github.com:brianlan/a.git")
    p2 = repos.project_create(conn, "b", "git@github.com:brianlan/b.git")
    adapter = _FakeAdapter(issues=[IssueDTO(10, "x", "open")])
    poll_all(conn, adapter)

    assert len(repos.github_issue_list(conn, p1.id)) == 1
    assert len(repos.github_issue_list(conn, p2.id)) == 1


def test_github_api_endpoints(client: TestClient, db_path: Path) -> None:
    db = get_db(db_path)
    with db.connect() as c:
        project = repos.project_create(c, "demo", "git@github.com:brianlan/demo.git")
        poll_project(
            c,
            project,
            _FakeAdapter(
                issues=[IssueDTO(1, "Issue", "open")],
                prs=[PullRequestDTO(2, "PR", "open", False)],
            ),
        )
        c.commit()

    issues_resp = client.get(f"/api/v1/projects/{project.id}/github/issues")
    assert issues_resp.status_code == 200
    assert issues_resp.json()[0]["issue_number"] == 1

    prs_resp = client.get(f"/api/v1/projects/{project.id}/github/pulls")
    assert prs_resp.status_code == 200
    assert prs_resp.json()[0]["pr_number"] == 2

    status_resp = client.get(f"/api/v1/projects/{project.id}/github/polling-status")
    assert status_resp.status_code == 200
    assert status_resp.json()["last_ok"] is True


def test_github_api_404_for_unknown_project(client: TestClient) -> None:
    assert client.get("/api/v1/projects/999/github/issues").status_code == 404
