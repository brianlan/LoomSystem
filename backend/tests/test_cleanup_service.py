"""Tests for destructive cleanup: project deletion cascade and hard-kill (T12).

Covers: cascade delete with no agents, cascade with reviewer+implementor,
project-isolation (only project-owned containers removed), hard-kill
preserving project record, hard-kill only killing running implementors,
audit events, already-missing container tolerance, and error cases.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cleanup_service as svc
from app import repositories as repos
from app.db import get_db
from app.docker import FakeDockerAdapter
from app.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "cleanup_test.db"
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


def _seed_project(conn: sqlite3.Connection, name: str = "demo") -> repos.Project:
    repos.agent_definition_create(conn, "agent", "# Prompt", "bot")
    repos.model_entry_create(conn, "anthropic", "claude", "sk-secret")
    repos.docker_image_create(conn, "loomsystem/runtime:latest")
    return repos.project_create(conn, name, "git@github.com:brianlan/demo.git")


def _populate_adapter(adapter: FakeDockerAdapter, container_id: str) -> None:
    """Pre-seed a container in the FakeDockerAdapter so stop/remove are observable."""
    adapter.containers[container_id] = {
        "image": "test",
        "name": f"loom-{container_id}",
        "env": "{}",
        "volumes": "[]",
        "labels": json.dumps({}),
        "state": "running",
    }


def _make_agent_instance(
    conn: sqlite3.Connection,
    project_id: int,
    agent_type: str = "reviewer",
    container_id: str | None = None,
    status: str = "running",
    credential_dir: str | None = None,
    adapter: FakeDockerAdapter | None = None,
) -> repos.AgentInstance:
    inst = repos.agent_instance_create(
        conn,
        project_id=project_id,
        agent_type=agent_type,
        container_id=container_id,
        container_name=f"loom-{container_id}" if container_id else None,
    )
    if status != "running":
        repos.agent_instance_update(conn, inst.id, status=status)
    if credential_dir:
        repos.config_snapshot_create(conn, inst.id, {"credential_dir": credential_dir})
    if container_id and adapter is not None:
        _populate_adapter(adapter, container_id)
    return inst


# ---------------------------------------------------------------------------
# delete_project_cascade — service-level tests
# ---------------------------------------------------------------------------


def test_cascade_delete_no_agents(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()

    svc.delete_project_cascade(conn, project.id, adapter)

    assert repos.project_get(conn, project.id) is None
    assert adapter.containers == {}


def test_cascade_delete_with_reviewer_and_implementor(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()
    cred_dir1 = str(tmp_path / "creds1")
    cred_dir2 = str(tmp_path / "creds2")
    Path(cred_dir1).mkdir()
    Path(cred_dir2).mkdir()

    _make_agent_instance(
        conn, project.id, "reviewer", "cid-r1",
        credential_dir=cred_dir1, adapter=adapter,
    )
    _make_agent_instance(
        conn, project.id, "implementor", "cid-i1",
        credential_dir=cred_dir2, adapter=adapter,
    )
    conn.commit()

    svc.delete_project_cascade(conn, project.id, adapter)

    # DB cascade wipes agent_instances + audit_events, so we verify
    # observable side-effects: containers removed, creds cleaned, project gone.
    assert repos.project_get(conn, project.id) is None
    assert "cid-r1" not in adapter.containers
    assert "cid-i1" not in adapter.containers
    assert not Path(cred_dir1).exists()
    assert not Path(cred_dir2).exists()


def test_cascade_delete_project_not_found(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    with pytest.raises(svc.CleanupError, match="not found"):
        svc.delete_project_cascade(conn, 999, adapter)


def test_cascade_delete_isolates_other_projects(
    conn: sqlite3.Connection
) -> None:
    project_a = _seed_project(conn, "project-a")
    project_b = repos.project_create(conn, "project-b", "git@github.com:b/b.git")
    adapter = FakeDockerAdapter()

    _make_agent_instance(conn, project_a.id, "reviewer", "cid-a1", adapter=adapter)
    inst_b = _make_agent_instance(conn, project_b.id, "reviewer", "cid-b1", adapter=adapter)
    conn.commit()

    svc.delete_project_cascade(conn, project_a.id, adapter)

    assert repos.project_get(conn, project_a.id) is None
    assert repos.project_get(conn, project_b.id) is not None
    assert "cid-a1" not in adapter.containers
    assert "cid-b1" in adapter.containers

    b_inst = repos.agent_instance_get(conn, inst_b.id)
    assert b_inst is not None and b_inst.status == "running"


def test_cascade_delete_instance_without_container(
    conn: sqlite3.Connection
) -> None:
    """Instance with no container_id should be tolerated (idempotent cleanup)."""
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()

    _make_agent_instance(conn, project.id, "reviewer", container_id=None)
    conn.commit()

    svc.delete_project_cascade(conn, project.id, adapter)

    assert repos.project_get(conn, project.id) is None


# ---------------------------------------------------------------------------
# hard_kill_implementors — service-level tests
# ---------------------------------------------------------------------------


def test_hard_kill_terminates_running_implementors(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()
    cred_dir = str(tmp_path / "creds")
    Path(cred_dir).mkdir()

    impl1 = _make_agent_instance(
        conn, project.id, "implementor", "cid-i1",
        credential_dir=cred_dir, adapter=adapter,
    )
    impl2 = _make_agent_instance(
        conn, project.id, "implementor", "cid-i2", adapter=adapter,
    )
    conn.commit()

    result = svc.hard_kill_implementors(conn, project.id, adapter)

    assert set(result.killed_instance_ids) == {impl1.id, impl2.id}
    assert "cid-i1" not in adapter.containers
    assert "cid-i2" not in adapter.containers
    assert not Path(cred_dir).exists()

    for iid in (impl1.id, impl2.id):
        inst = repos.agent_instance_get(conn, iid)
        assert inst is not None and inst.status == "terminated"


def test_hard_kill_preserves_project_and_reviewers(
    conn: sqlite3.Connection
) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()

    reviewer = _make_agent_instance(
        conn, project.id, "reviewer", "cid-r1", adapter=adapter,
    )
    _make_agent_instance(conn, project.id, "implementor", "cid-i1", adapter=adapter)
    conn.commit()

    result = svc.hard_kill_implementors(conn, project.id, adapter)

    assert len(result.killed_instance_ids) == 1
    assert repos.project_get(conn, project.id) is not None
    assert "cid-r1" in adapter.containers
    r = repos.agent_instance_get(conn, reviewer.id)
    assert r is not None and r.status == "running"


def test_hard_kill_skips_already_terminated(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()

    running_impl = _make_agent_instance(
        conn, project.id, "implementor", "cid-i1", adapter=adapter,
    )
    _make_agent_instance(
        conn, project.id, "implementor", "cid-i2",
        status="terminated", adapter=adapter,
    )
    conn.commit()

    result = svc.hard_kill_implementors(conn, project.id, adapter)

    assert result.killed_instance_ids == [running_impl.id]
    # Already-terminated instance's container is not touched.
    assert "cid-i2" in adapter.containers


def test_hard_kill_no_running_implementors(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()

    result = svc.hard_kill_implementors(conn, project.id, adapter)

    assert result.killed_instance_ids == []


def test_hard_kill_project_not_found(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    with pytest.raises(svc.CleanupError, match="not found"):
        svc.hard_kill_implementors(conn, 999, adapter)


def test_hard_kill_records_audit_event(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()

    i1 = _make_agent_instance(conn, project.id, "implementor", "cid-i1", adapter=adapter)
    i2 = _make_agent_instance(conn, project.id, "implementor", "cid-i2", adapter=adapter)
    conn.commit()

    svc.hard_kill_implementors(conn, project.id, adapter)

    events = repos.audit_event_list(conn, project_id=project.id)
    kill_events = [e for e in events if e["event_type"] == "implementor_hard_kill"]
    assert len(kill_events) == 1
    assert set(kill_events[0]["payload"]["killed_instances"]) == {i1.id, i2.id}


# ---------------------------------------------------------------------------
# API-level tests
# ---------------------------------------------------------------------------


def test_api_delete_project_no_agents(client: TestClient) -> None:
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project = _seed_project(conn)
        conn.commit()

    resp = client.delete(f"/api/v1/projects/{project.id}")
    assert resp.status_code == 200
    assert client.get(f"/api/v1/projects/{project.id}").status_code == 404


def test_api_delete_project_cascade(
    client: TestClient
) -> None:
    adapter = FakeDockerAdapter()
    app.state.docker_adapter = adapter
    try:
        db = get_db(app.state.db.db_path)
        with db.connect() as conn:
            project = _seed_project(conn)
            _make_agent_instance(conn, project.id, "reviewer", "cid-r1", adapter=adapter)
            _make_agent_instance(conn, project.id, "implementor", "cid-i1", adapter=adapter)
            conn.commit()
            project_id = project.id

        resp = client.delete(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 200
        assert "cid-r1" not in adapter.containers
        assert "cid-i1" not in adapter.containers

        with db.connect() as conn:
            assert repos.project_get(conn, project_id) is None
    finally:
        del app.state.docker_adapter


def test_api_hard_kill_implementors(client: TestClient) -> None:
    adapter = FakeDockerAdapter()
    app.state.docker_adapter = adapter
    try:
        db = get_db(app.state.db.db_path)
        with db.connect() as conn:
            project = _seed_project(conn)
            _make_agent_instance(conn, project.id, "reviewer", "cid-r1", adapter=adapter)
            _make_agent_instance(conn, project.id, "implementor", "cid-i1", adapter=adapter)
            _make_agent_instance(conn, project.id, "implementor", "cid-i2", adapter=adapter)
            conn.commit()
            project_id = project.id

        resp = client.post(f"/api/v1/projects/{project_id}/hard-kill-implementors")
        assert resp.status_code == 200
        assert "2" in resp.json()["message"]

        # Reviewer untouched, project preserved.
        assert "cid-r1" in adapter.containers
        assert "cid-i1" not in adapter.containers
        assert "cid-i2" not in adapter.containers
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 200
    finally:
        del app.state.docker_adapter


def test_api_hard_kill_not_found(client: TestClient) -> None:
    resp = client.post("/api/v1/projects/999/hard-kill-implementors")
    assert resp.status_code == 404
