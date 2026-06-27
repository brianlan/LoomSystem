"""Tests for audit trail, notifications, and aggregate status (T15).

Covers OBS-1/OBS-3/OBS-5 and FR-35:
- Audit events for launch (container_start), termination (container_stop/remove),
  state transitions, PR-opened, issue-closed, and container die.
- Notifications for permanent launch failure, PR opened, issue resolved.
- Per-agent audit browsing API.
- Notification feed API + mark-read.
- Aggregate dashboard status API.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import repositories as repos
from app import reviewer_service as svc
from app.container_monitor import _handle_dead_container
from app.db import get_db
from app.docker import FakeDockerAdapter
from app.github import IssueDTO, PullRequestDTO
from app.implementor_loop import (
    STATE_DRAINING,
    STATE_IDLE,
    STATE_RUNNING,
    check_draining_complete,
    soft_stop,
    start_loop,
)
from app.main import app
from app.polling import poll_project
from app.reviewer_service import terminate_reviewer

_IMAGE = "loomsystem/runtime:latest"
_FAKE_SSH_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
)


class _FakeGitHubAdapter:
    def __init__(
        self,
        issues: list[IssueDTO] | None = None,
        prs: list[PullRequestDTO] | None = None,
    ) -> None:
        self._issues = issues or []
        self._prs = prs or []

    def list_open_issues(self, owner: str, repo: str) -> list[IssueDTO]:
        return self._issues

    def list_open_pull_requests(self, owner: str, repo: str) -> list[PullRequestDTO]:
        return self._prs

    def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequestDTO:
        raise NotImplementedError

    def get_issue(self, owner: str, repo: str, number: int) -> IssueDTO:
        raise NotImplementedError

    def update_pr(self, owner: str, repo: str, number: int, body: str) -> None:
        raise NotImplementedError


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "audit_test.db"
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


def _seed_reviewer_project(conn: sqlite3.Connection) -> repos.Project:
    repos.agent_definition_create(conn, "reviewer", "# Reviewer", "bot")
    repos.model_entry_create(conn, "anthropic", "claude-3", "sk-secret")
    repos.docker_image_create(conn, _IMAGE)
    repos.setting_set(conn, "ssh_key", _FAKE_SSH_KEY)
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    repos.project_update(
        conn,
        project.id,
        reviewer_config={
            "agent_definition_id": 1,
            "model_entry_id": 1,
            "docker_image_id": 1,
            "trigger_interval_minutes": 15,
            "reviewer_cap": 1,
        },
    )
    conn.commit()
    return project


def _make_adapter() -> FakeDockerAdapter:
    adapter = FakeDockerAdapter()
    adapter.images.add(_IMAGE)
    adapter.exec_default = (0, "anthropic")
    return adapter


# ---------------------------------------------------------------------------
# Audit events: launch / terminate / state transitions
# ---------------------------------------------------------------------------


def test_launch_emits_container_start_audit(conn: sqlite3.Connection) -> None:
    project = _seed_reviewer_project(conn)
    result = svc.launch_reviewer(conn, project.id, _make_adapter())

    events = repos.audit_event_list(conn, agent_instance_id=result.agent_instance_id)
    types = [e["event_type"] for e in events]
    assert "container_start" in types
    start_event = next(e for e in events if e["event_type"] == "container_start")
    assert start_event["payload"]["container_id"] == result.container_id


def test_terminate_emits_container_stop_and_remove_audit(
    conn: sqlite3.Connection,
) -> None:
    project = _seed_reviewer_project(conn)
    adapter = _make_adapter()
    result = svc.launch_reviewer(conn, project.id, adapter)

    terminate_reviewer(conn, result.agent_instance_id, adapter)

    events = repos.audit_event_list(conn, agent_instance_id=result.agent_instance_id)
    types = [e["event_type"] for e in events]
    assert "container_stop" in types
    assert "container_remove" in types


def test_state_transition_audit_on_loop_start_and_drain(conn: sqlite3.Connection) -> None:
    project = repos.project_create(conn, "p", "git@github.com:brianlan/p.git")
    conn.commit()

    start_loop(conn, project.id)
    soft_stop(conn, project.id)

    events = repos.audit_event_list(conn, project_id=project.id)
    transitions = [e["payload"]["to"] for e in events if e["event_type"] == "state_transition"]
    assert STATE_RUNNING in transitions
    assert STATE_DRAINING in transitions


def test_state_transition_audit_on_draining_to_idle(conn: sqlite3.Connection) -> None:
    project = repos.project_create(conn, "p", "git@github.com:brianlan/p.git")
    conn.commit()
    start_loop(conn, project.id)
    soft_stop(conn, project.id)
    conn.commit()

    # No running implementors -> draining completes immediately.
    assert check_draining_complete(conn, project.id) is True

    events = repos.audit_event_list(conn, project_id=project.id)
    transitions = [e["payload"]["to"] for e in events if e["event_type"] == "state_transition"]
    assert STATE_IDLE in transitions


# ---------------------------------------------------------------------------
# Audit events + notifications: PR-opened, issue-closed (polling)
# ---------------------------------------------------------------------------


def test_poll_emits_pr_opened_audit_and_notification(conn: sqlite3.Connection) -> None:
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    conn.commit()
    poll_project(
        conn,
        project,
        _FakeGitHubAdapter(prs=[PullRequestDTO(5, "New PR", "open", False)]),
    )
    conn.commit()

    events = repos.audit_event_list(conn, project_id=project.id)
    assert any(e["event_type"] == "pr_opened" for e in events)
    notes = repos.notification_list(conn, project_id=project.id)
    assert any("PR #5" in n["message"] for n in notes)


def test_poll_does_not_renotify_existing_pr(conn: sqlite3.Connection) -> None:
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    conn.commit()
    prs = [PullRequestDTO(5, "PR", "open", False)]
    poll_project(conn, project, _FakeGitHubAdapter(prs=prs))
    conn.commit()
    poll_project(conn, project, _FakeGitHubAdapter(prs=prs))
    conn.commit()

    notes = repos.notification_list(conn, project_id=project.id)
    pr_notes = [n for n in notes if "PR #5" in n["message"]]
    assert len(pr_notes) == 1


def test_poll_emits_issue_closed_audit_and_notification(conn: sqlite3.Connection) -> None:
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    conn.commit()
    # Issue appears open, then disappears (closed upstream).
    poll_project(conn, project, _FakeGitHubAdapter(issues=[IssueDTO(3, "Three", "open")]))
    conn.commit()
    poll_project(conn, project, _FakeGitHubAdapter(issues=[]))
    conn.commit()

    events = repos.audit_event_list(conn, project_id=project.id)
    assert any(e["event_type"] == "issue_closed" for e in events)
    notes = repos.notification_list(conn, project_id=project.id)
    assert any("Issue #3" in n["message"] and "resolved" in n["message"] for n in notes)


# ---------------------------------------------------------------------------
# Notifications: permanent launch failure
# ---------------------------------------------------------------------------


def test_launch_failure_emits_permanent_failure_notification(
    conn: sqlite3.Connection,
) -> None:
    project = _seed_reviewer_project(conn)
    adapter = FakeDockerAdapter()
    adapter.images.add(_IMAGE)
    # Force the in-container clone step to fail.
    adapter.exec_default = (1, "clone failed")

    with pytest.raises(svc.ReviewerError):
        svc.launch_reviewer(conn, project.id, adapter)

    notes = repos.notification_list(conn, project_id=project.id)
    assert any("launch failed" in n["message"].lower() for n in notes)


# ---------------------------------------------------------------------------
# Audit events: container die (container monitor)
# ---------------------------------------------------------------------------


def test_dead_container_emits_die_audit(conn: sqlite3.Connection) -> None:
    project = _seed_reviewer_project(conn)
    adapter = _make_adapter()
    result = svc.launch_reviewer(conn, project.id, adapter)
    conn.commit()

    instance = repos.agent_instance_get(conn, result.agent_instance_id)
    assert instance is not None
    # High retry cap so we exercise the die path without permanent failure.
    _handle_dead_container(conn, instance, adapter, retry_cap=10, now=0.0)

    events = repos.audit_event_list(conn, agent_instance_id=result.agent_instance_id)
    assert any(e["event_type"] == "container_die" for e in events)


# ---------------------------------------------------------------------------
# APIs: per-agent audit, notification feed, mark-read
# ---------------------------------------------------------------------------


def test_audit_api_endpoints(client: TestClient, conn: sqlite3.Connection) -> None:
    project = _seed_reviewer_project(conn)
    result = svc.launch_reviewer(conn, project.id, _make_adapter())
    conn.commit()

    # Per-agent audit.
    resp = client.get(f"/api/v1/agents/{result.agent_instance_id}/audit")
    assert resp.status_code == 200
    types = [e["event_type"] for e in resp.json()]
    assert "container_start" in types

    # Per-project audit.
    resp = client.get(f"/api/v1/projects/{project.id}/audit")
    assert resp.status_code == 200
    assert any(e["event_type"] == "container_start" for e in resp.json())

    # Global audit.
    resp = client.get("/api/v1/audit")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    # Unknown agent -> 404.
    assert client.get("/api/v1/agents/999/audit").status_code == 404


def test_notification_api_list_and_mark_read(client: TestClient, db_path: Path) -> None:
    db = get_db(db_path)
    with db.connect() as c:
        project = repos.project_create(c, "demo", "git@github.com:brianlan/demo.git")
        nid = repos.notification_create(c, "Hello", project_id=project.id)
        c.commit()

    resp = client.get("/api/v1/notifications")
    assert resp.status_code == 200
    assert any(n["id"] == nid for n in resp.json())

    resp = client.get("/api/v1/notifications", params={"unread_only": True})
    assert resp.status_code == 200
    assert any(n["id"] == nid for n in resp.json())

    resp = client.post(f"/api/v1/notifications/{nid}/read")
    assert resp.status_code == 200

    resp = client.get("/api/v1/notifications", params={"unread_only": True})
    assert resp.status_code == 200
    assert all(n["id"] != nid for n in resp.json())

    # Unknown notification -> 404.
    assert client.post("/api/v1/notifications/999/read").status_code == 404


def test_project_notification_api(client: TestClient, db_path: Path) -> None:
    db = get_db(db_path)
    with db.connect() as c:
        project = repos.project_create(c, "demo", "git@github.com:brianlan/demo.git")
        repos.notification_create(c, "Project note", project_id=project.id)
        c.commit()

    resp = client.get(f"/api/v1/projects/{project.id}/notifications")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    assert client.get("/api/v1/projects/999/notifications").status_code == 404


# ---------------------------------------------------------------------------
# Aggregate status API (OBS-5)
# ---------------------------------------------------------------------------


def test_aggregate_status_api(client: TestClient, conn: sqlite3.Connection) -> None:
    project = _seed_reviewer_project(conn)
    svc.launch_reviewer(conn, project.id, _make_adapter())
    conn.commit()

    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running_reviewers"] == 1
    assert body["running_implementors"] == 0
    assert "backlog_size" in body
    assert isinstance(body["recent_failures"], list)


def test_aggregate_status_backlog_and_failures(conn: sqlite3.Connection) -> None:
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    conn.commit()
    # Seed two open unassigned issues + one in-progress (not backlog).
    poll_project(
        conn,
        project,
        _FakeGitHubAdapter(issues=[IssueDTO(1, "A", "open"), IssueDTO(2, "B", "open")]),
    )
    conn.commit()
    repos.github_issue_set_status(conn, project.id, 2, repos.ISSUE_STATUS_IN_PROGRESS)
    conn.commit()

    # Seed a failed triage run.
    repos.triage_run_create_full(
        conn,
        project_id=project.id,
        ranked_issue_ids=[],
        status="failed",
        attempts=10,
        error="boom",
    )
    conn.commit()

    status = repos.aggregate_status(conn)
    assert status["backlog_size"] == 1  # only issue #1 still unassigned
    assert any(f["kind"] == "triage" for f in status["recent_failures"])
