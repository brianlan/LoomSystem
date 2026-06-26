"""Tests for the reviewer lifecycle service (T08).

Covers: launch success, cap enforcement, manual trigger, termination
(stop+remove container, credential cleanup, status update), status
projection, and error cases for missing project/config.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import repositories as repos
from app import reviewer_service as svc
from app.db import get_db
from app.docker import FakeDockerAdapter
from app.main import app
from app.trigger import TriggerService

_IMAGE = "loomsystem/runtime:latest"
_FAKE_SSH_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "reviewer_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


def _seed_project(
    conn: sqlite3.Connection,
    *,
    reviewer_cap: int = 1,
    interval_minutes: int = 15,
) -> repos.Project:
    """Seed registry entries, global settings, and a project with reviewer config."""
    repos.agent_definition_create(conn, "reviewer", "# Reviewer prompt", "bot")
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
            "trigger_interval_minutes": interval_minutes,
            "reviewer_cap": reviewer_cap,
        },
    )
    conn.commit()
    return project


def _make_adapter() -> FakeDockerAdapter:
    """FakeDockerAdapter with image pre-seed and exec responses for launch."""
    adapter = FakeDockerAdapter()
    adapter.images.add(_IMAGE)
    # launch_agent runs two execs: git clone + opencode models.
    adapter.exec_default = (0, "anthropic")
    return adapter


# ---------------------------------------------------------------------------
# Launch tests
# ---------------------------------------------------------------------------


def test_launch_reviewer_success(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()

    result = svc.launch_reviewer(conn, project.id, adapter)

    assert result.agent_instance_id > 0
    assert result.container_id.startswith("fake-container-")
    assert result.container_name.startswith("loom-")

    instance = repos.agent_instance_get(conn, result.agent_instance_id)
    assert instance is not None
    assert instance.agent_type == "reviewer"
    assert instance.status == "running"
    assert instance.container_id == result.container_id

    # Audit event recorded.
    events = repos.audit_event_list(conn, project_id=project.id)
    assert any(e["event_type"] == "reviewer_launch" for e in events)


def test_launch_reviewer_creates_container_in_adapter(
    conn: sqlite3.Connection,
) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()

    result = svc.launch_reviewer(conn, project.id, adapter)

    assert result.container_id in adapter.containers
    c = adapter.containers[result.container_id]
    assert c["image"] == _IMAGE


def test_launch_reviewer_cap_enforced_default(conn: sqlite3.Connection) -> None:
    """FR-5: default cap = 1. Second launch is rejected."""
    project = _seed_project(conn, reviewer_cap=1)
    adapter = _make_adapter()

    svc.launch_reviewer(conn, project.id, adapter)

    with pytest.raises(svc.ReviewerError, match="cap"):
        svc.launch_reviewer(conn, project.id, adapter)

    # Still only 1 running reviewer.
    running = sum(
        1
        for inst in repos.agent_instance_list_for_project(conn, project.id)
        if inst.agent_type == "reviewer" and inst.status == "running"
    )
    assert running == 1


def test_launch_reviewer_cap_enforced_custom(conn: sqlite3.Connection) -> None:
    """Cap = 2 allows two reviewers, rejects the third."""
    project = _seed_project(conn, reviewer_cap=2)
    adapter = _make_adapter()

    svc.launch_reviewer(conn, project.id, adapter)
    svc.launch_reviewer(conn, project.id, adapter)

    with pytest.raises(svc.ReviewerError, match="cap"):
        svc.launch_reviewer(conn, project.id, adapter)


def test_launch_reviewer_project_not_found(conn: sqlite3.Connection) -> None:
    adapter = _make_adapter()
    with pytest.raises(svc.ReviewerError, match="not found"):
        svc.launch_reviewer(conn, 9999, adapter)


def test_launch_reviewer_missing_ssh_key(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    repos.setting_set(conn, "ssh_key", "")
    conn.commit()
    adapter = _make_adapter()

    with pytest.raises(svc.ReviewerError, match="SSH key"):
        svc.launch_reviewer(conn, project.id, adapter)


def test_launch_reviewer_missing_agent_definition(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    # Bypass repo validation to simulate a stale/orphaned FK reference.
    import json

    conn.execute(
        "UPDATE projects SET reviewer_config_json = ? WHERE id = ?",
        (
            json.dumps(
                {
                    "agent_definition_id": 9999,
                    "model_entry_id": 1,
                    "docker_image_id": 1,
                    "trigger_interval_minutes": 15,
                    "reviewer_cap": 1,
                }
            ),
            project.id,
        ),
    )
    conn.commit()
    adapter = _make_adapter()

    with pytest.raises(svc.ReviewerError, match="agent definition"):
        svc.launch_reviewer(conn, project.id, adapter)


def test_launch_after_terminate_allows_relaunch(
    conn: sqlite3.Connection,
) -> None:
    """Terminated reviewer doesn't count toward cap."""
    project = _seed_project(conn, reviewer_cap=1)
    adapter = _make_adapter()

    result = svc.launch_reviewer(conn, project.id, adapter)
    svc.terminate_reviewer(conn, result.agent_instance_id, adapter)

    # Cap slot freed — new launch should succeed.
    result2 = svc.launch_reviewer(conn, project.id, adapter)
    assert result2.agent_instance_id != result.agent_instance_id


# ---------------------------------------------------------------------------
# Trigger tests
# ---------------------------------------------------------------------------


def test_trigger_reviewer_manual(conn: sqlite3.Connection) -> None:
    """FR-19: manual trigger fires for a running reviewer."""
    project = _seed_project(conn)
    adapter = _make_adapter()
    launch_result = svc.launch_reviewer(conn, project.id, adapter)

    # Set up streaming exec for the trigger command.
    # Session ID must be >= 8 chars to match extract_session_id's regex.
    adapter.exec_stream_default = (['session abc12345'], 0)

    trigger_service = TriggerService(adapter)
    outcome = svc.trigger_reviewer(conn, project.id, trigger_service, manual=True)

    assert outcome.exit_code == 0
    assert outcome.captured_session_id == "abc12345"

    # Session id persisted on the instance.
    instance = repos.agent_instance_get(conn, launch_result.agent_instance_id)
    assert instance is not None
    assert instance.session_id == "abc12345"


def test_trigger_reviewer_no_running(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()
    trigger_service = TriggerService(adapter)

    with pytest.raises(svc.ReviewerError, match="No running reviewer"):
        svc.trigger_reviewer(conn, project.id, trigger_service)


def test_trigger_reviewer_project_not_found(conn: sqlite3.Connection) -> None:
    adapter = _make_adapter()
    trigger_service = TriggerService(adapter)
    with pytest.raises(svc.ReviewerError, match="not found"):
        svc.trigger_reviewer(conn, 9999, trigger_service)


# ---------------------------------------------------------------------------
# Terminate tests
# ---------------------------------------------------------------------------


def test_terminate_reviewer_stops_and_removes_container(
    conn: sqlite3.Connection,
) -> None:
    """FR-20: termination stops and removes the container."""
    project = _seed_project(conn)
    adapter = _make_adapter()
    result = svc.launch_reviewer(conn, project.id, adapter)
    container_id = result.container_id

    assert container_id in adapter.containers

    svc.terminate_reviewer(conn, result.agent_instance_id, adapter)

    # Container removed from adapter.
    assert container_id not in adapter.containers

    # Instance marked terminated.
    instance = repos.agent_instance_get(conn, result.agent_instance_id)
    assert instance is not None
    assert instance.status == "terminated"

    # Audit event recorded.
    events = repos.audit_event_list(conn, agent_instance_id=result.agent_instance_id)
    assert any(e["event_type"] == "reviewer_terminate" for e in events)


def test_terminate_reviewer_not_found(conn: sqlite3.Connection) -> None:
    adapter = _make_adapter()
    with pytest.raises(svc.ReviewerError, match="not found"):
        svc.terminate_reviewer(conn, 9999, adapter)


def test_terminate_wrong_agent_type(conn: sqlite3.Connection) -> None:
    """Terminating an implementor instance via reviewer API fails."""
    project = _seed_project(conn)
    instance = repos.agent_instance_create(
        conn, project_id=project.id, agent_type="implementor"
    )
    conn.commit()
    adapter = _make_adapter()

    with pytest.raises(svc.ReviewerError, match="not a reviewer"):
        svc.terminate_reviewer(conn, instance.id, adapter)


# ---------------------------------------------------------------------------
# Status tests
# ---------------------------------------------------------------------------


def test_get_status_empty(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    status = svc.get_reviewer_status(conn, project.id)

    assert status["project_id"] == project.id
    assert status["reviewer_cap"] == 1
    assert status["running_reviewers"] == 0
    assert status["reviewers"] == []


def test_get_status_with_running(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn, reviewer_cap=2)
    adapter = _make_adapter()
    svc.launch_reviewer(conn, project.id, adapter)
    svc.launch_reviewer(conn, project.id, adapter)

    status = svc.get_reviewer_status(conn, project.id)

    reviewers = status["reviewers"]
    assert isinstance(reviewers, list)
    assert len(reviewers) == 2
    r = reviewers[0]
    assert r["agent_instance_id"] > 0
    assert r["container_id"] is not None
    assert r["status"] == "running"


def test_get_status_excludes_terminated(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()
    result = svc.launch_reviewer(conn, project.id, adapter)
    svc.terminate_reviewer(conn, result.agent_instance_id, adapter)

    status = svc.get_reviewer_status(conn, project.id)

    assert status["running_reviewers"] == 0
    assert status["reviewers"] == []


def test_get_status_project_not_found(conn: sqlite3.Connection) -> None:
    with pytest.raises(svc.ReviewerError, match="not found"):
        svc.get_reviewer_status(conn, 9999)


# ---------------------------------------------------------------------------
# API-level tests (through TestClient)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "reviewer_api_test.db"
    app.state.db.db_path = db_path
    get_db(db_path).reset()
    return TestClient(app)


@pytest.fixture
def seeded_client(client: TestClient) -> TestClient:
    """Seed project + settings + FakeDockerAdapter on app.state."""
    adapter = FakeDockerAdapter()
    adapter.images.add(_IMAGE)
    adapter.exec_default = (0, "anthropic")
    app.state.docker_adapter = adapter
    app.state.trigger_service = TriggerService(adapter)

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        _seed_project(conn)
        conn.commit()
    return client


def _project_id(client: TestClient) -> int:
    resp = client.get("/api/v1/projects")
    return int(resp.json()[0]["id"])


def test_api_launch_reviewer(seeded_client: TestClient) -> None:
    pid = _project_id(seeded_client)
    resp = seeded_client.post(f"/api/v1/projects/{pid}/reviewers/launch")
    assert resp.status_code == 201
    data = resp.json()
    assert data["agent_instance_id"] > 0
    assert data["container_id"].startswith("fake-container-")


def test_api_launch_cap_enforced(seeded_client: TestClient) -> None:
    pid = _project_id(seeded_client)
    resp1 = seeded_client.post(f"/api/v1/projects/{pid}/reviewers/launch")
    assert resp1.status_code == 201

    resp2 = seeded_client.post(f"/api/v1/projects/{pid}/reviewers/launch")
    assert resp2.status_code == 409
    assert "cap" in resp2.json()["detail"].lower()


def test_api_terminate(seeded_client: TestClient) -> None:
    pid = _project_id(seeded_client)
    launch_resp = seeded_client.post(f"/api/v1/projects/{pid}/reviewers/launch")
    instance_id = launch_resp.json()["agent_instance_id"]

    resp = seeded_client.post(
        f"/api/v1/projects/{pid}/reviewers/{instance_id}/terminate"
    )
    assert resp.status_code == 200

    status_resp = seeded_client.get(f"/api/v1/projects/{pid}/reviewers/status")
    assert status_resp.json()["running_reviewers"] == 0


def test_api_trigger(seeded_client: TestClient) -> None:
    pid = _project_id(seeded_client)
    launch_resp = seeded_client.post(f"/api/v1/projects/{pid}/reviewers/launch")
    instance_id = launch_resp.json()["agent_instance_id"]

    adapter: FakeDockerAdapter = app.state.docker_adapter
    adapter.exec_stream_default = (['session xyz78901'], 0)

    resp = seeded_client.post(
        f"/api/v1/projects/{pid}/reviewers/{instance_id}/trigger"
    )
    assert resp.status_code == 200


def test_api_status_empty(seeded_client: TestClient) -> None:
    pid = _project_id(seeded_client)
    resp = seeded_client.get(f"/api/v1/projects/{pid}/reviewers/status")
    assert resp.status_code == 200
    assert resp.json()["running_reviewers"] == 0


def test_api_launch_project_not_found(seeded_client: TestClient) -> None:
    resp = seeded_client.post("/api/v1/projects/9999/reviewers/launch")
    assert resp.status_code == 404
