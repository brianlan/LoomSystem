"""Tests for the implementor lifecycle HTTP API (T17)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import repositories as repos
from app.db import get_db
from app.docker import FakeDockerAdapter
from app.main import app

_IMAGE = "loomsystem/runtime:latest"
_FAKE_SSH_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "implementors_api_test.db"
    app.state.db.db_path = db_path
    get_db(db_path).reset()
    adapter = FakeDockerAdapter()
    adapter.images.add(_IMAGE)
    adapter.exec_default = (0, "anthropic")
    app.state.docker_adapter = adapter
    return TestClient(app)


@pytest.fixture
def registry_ids(client: TestClient) -> dict[str, int]:
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        agent = repos.agent_definition_create(conn, "implementor", "# Implementor", "bot")
        model = repos.model_entry_create(conn, "anthropic", "claude", "sk-secret")
        image = repos.docker_image_create(conn, _IMAGE)
        repos.setting_set(conn, "ssh_key", _FAKE_SSH_KEY)
        conn.commit()
        return {
            "agent_definition_id": agent.id,
            "model_entry_id": model.id,
            "docker_image_id": image.id,
        }


def _project_payload(registry_ids: dict[str, int]) -> dict[str, Any]:
    return {
        "name": "demo",
        "repo_url": "git@github.com:brianlan/demo.git",
        "reviewer_config": {
            "agent_definition_id": registry_ids["agent_definition_id"],
            "model_entry_id": registry_ids["model_entry_id"],
            "docker_image_id": registry_ids["docker_image_id"],
            "trigger_interval_minutes": 15,
            "reviewer_cap": 1,
        },
        "implementor_config": {
            "agent_definition_id": registry_ids["agent_definition_id"],
            "model_entry_id": registry_ids["model_entry_id"],
            "docker_image_id": registry_ids["docker_image_id"],
            "trigger_interval_minutes": 15,
            "parallelism": 2,
        },
    }


def _seed_issue(conn: sqlite3.Connection, project_id: int, issue_number: int = 1) -> None:
    conn.execute(
        "INSERT INTO github_issues (project_id, issue_number, title, state, loom_status) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, issue_number, f"Issue {issue_number}", "open", "unassigned"),
    )


def _create_project(client: TestClient, registry_ids: dict[str, int], name: str = "demo") -> int:
    payload = _project_payload(registry_ids)
    payload["name"] = name
    resp = client.post("/api/v1/projects", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_launch_implementor_success(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    project_id = _create_project(client, registry_ids)
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        _seed_issue(conn, project_id, 5)
        conn.commit()

    resp = client.post(f"/api/v1/projects/{project_id}/implementors", json={"issue_number": 5})
    assert resp.status_code == 201
    data = resp.json()
    assert data["agent_instance_id"] > 0
    assert data["container_id"].startswith("fake-container-")

    status = client.get(f"/api/v1/projects/{project_id}/implementors/status").json()
    assert status["state"] == "idle"
    assert status["running_implementors"] == 1
    assert any(i["issue_number"] == 5 for i in status["implementors"])


def test_launch_implementor_missing_issue(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    project_id = _create_project(client, registry_ids)
    resp = client.post(f"/api/v1/projects/{project_id}/implementors", json={"issue_number": 99})
    assert resp.status_code == 404


def test_launch_implementor_missing_project(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    resp = client.post("/api/v1/projects/999/implementors", json={"issue_number": 1})
    assert resp.status_code == 404


def test_terminate_implementor_success(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    project_id = _create_project(client, registry_ids)
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        _seed_issue(conn, project_id, 3)
        conn.commit()

    launch = client.post(f"/api/v1/projects/{project_id}/implementors", json={"issue_number": 3})
    instance_id = launch.json()["agent_instance_id"]

    resp = client.post(f"/api/v1/projects/{project_id}/implementors/{instance_id}/terminate")
    assert resp.status_code == 200
    assert resp.json()["message"] == f"Implementor {instance_id} terminated"

    status = client.get(f"/api/v1/projects/{project_id}/implementors/status").json()
    assert status["running_implementors"] == 0


def test_terminate_implementor_wrong_project(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    project_id = _create_project(client, registry_ids, name="demo")
    other_id = _create_project(client, registry_ids, name="other")
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        _seed_issue(conn, project_id, 3)
        conn.commit()

    launch = client.post(f"/api/v1/projects/{project_id}/implementors", json={"issue_number": 3})
    instance_id = launch.json()["agent_instance_id"]

    resp = client.post(f"/api/v1/projects/{other_id}/implementors/{instance_id}/terminate")
    assert resp.status_code == 404


def test_loop_start_soft_stop_hard_stop(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    project_id = _create_project(client, registry_ids)

    start = client.post(f"/api/v1/projects/{project_id}/implementors/loop/start")
    assert start.status_code == 200
    status = client.get(f"/api/v1/projects/{project_id}/implementors/status").json()
    assert status["state"] == "running"

    soft = client.post(f"/api/v1/projects/{project_id}/implementors/loop/soft-stop")
    assert soft.status_code == 200
    status = client.get(f"/api/v1/projects/{project_id}/implementors/status").json()
    assert status["state"] == "draining"

    hard = client.post(f"/api/v1/projects/{project_id}/implementors/loop/hard-stop")
    assert hard.status_code == 200
    status = client.get(f"/api/v1/projects/{project_id}/implementors/status").json()
    assert status["state"] == "idle"


def test_loop_start_already_running_returns_conflict(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    project_id = _create_project(client, registry_ids)
    assert client.post(f"/api/v1/projects/{project_id}/implementors/loop/start").status_code == 200
    resp = client.post(f"/api/v1/projects/{project_id}/implementors/loop/start")
    assert resp.status_code == 409


def test_hard_stop_terminates_running_implementors(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    project_id = _create_project(client, registry_ids)
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        _seed_issue(conn, project_id, 7)
        conn.commit()

    client.post(f"/api/v1/projects/{project_id}/implementors/loop/start")
    client.post(f"/api/v1/projects/{project_id}/implementors", json={"issue_number": 7})

    status_before = client.get(f"/api/v1/projects/{project_id}/implementors/status").json()
    assert status_before["running_implementors"] == 1

    resp = client.post(f"/api/v1/projects/{project_id}/implementors/loop/hard-stop")
    assert resp.status_code == 200

    status_after = client.get(f"/api/v1/projects/{project_id}/implementors/status").json()
    assert status_after["state"] == "idle"
    assert status_after["running_implementors"] == 0


def test_hard_kill_implementors_backwards_compatible(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    project_id = _create_project(client, registry_ids)
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        _seed_issue(conn, project_id, 2)
        conn.commit()

    client.post(f"/api/v1/projects/{project_id}/implementors", json={"issue_number": 2})
    resp = client.post(f"/api/v1/projects/{project_id}/implementors/hard-kill")
    assert resp.status_code == 200
    assert "1 implementor(s)" in resp.json()["message"]


def test_status_missing_project(client: TestClient) -> None:
    resp = client.get("/api/v1/projects/999/implementors/status")
    assert resp.status_code == 404
