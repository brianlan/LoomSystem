from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.repositories import (
    agent_definition_create,
    agent_instance_create,
    config_snapshot_create,
    config_snapshot_get,
    docker_image_create,
    model_entry_create,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "projects_api_test.db"
    app.state.db.db_path = db_path
    get_db(db_path).reset()
    return TestClient(app)


@pytest.fixture
def registry_ids(client: TestClient) -> dict[str, int]:
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        agent = agent_definition_create(conn, "reviewer", "# Reviewer", "bot")
        model = model_entry_create(conn, "anthropic", "claude", "sk-secret")
        image = docker_image_create(conn, "loomsystem/runtime:latest")
        conn.commit()
        return {
            "agent_definition_id": agent.id,
            "model_entry_id": model.id,
            "docker_image_id": image.id,
        }


def _reviewer_config(registry_ids: dict[str, int], **overrides: object) -> dict[str, object]:
    return {
        "agent_definition_id": registry_ids["agent_definition_id"],
        "model_entry_id": registry_ids["model_entry_id"],
        "docker_image_id": registry_ids["docker_image_id"],
        "trigger_interval_minutes": 15,
        "reviewer_cap": 1,
        **overrides,
    }


def _implementor_config(registry_ids: dict[str, int], **overrides: object) -> dict[str, object]:
    return {
        "agent_definition_id": registry_ids["agent_definition_id"],
        "model_entry_id": registry_ids["model_entry_id"],
        "docker_image_id": registry_ids["docker_image_id"],
        "trigger_interval_minutes": 15,
        "parallelism": 2,
        **overrides,
    }


def test_project_crud(client: TestClient, registry_ids: dict[str, int]) -> None:
    payload = {
        "name": "demo",
        "repo_url": "git@github.com:brianlan/demo.git",
        "reviewer_config": _reviewer_config(registry_ids),
        "implementor_config": _implementor_config(registry_ids),
    }
    create_resp = client.post("/api/v1/projects", json=payload)
    assert create_resp.status_code == 201
    data = create_resp.json()
    assert data["name"] == "demo"
    assert data["repo_url"] == "git@github.com:brianlan/demo.git"
    project_id = data["id"]

    list_resp = client.get("/api/v1/projects")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    get_resp = client.get(f"/api/v1/projects/{project_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "demo"

    patch_resp = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"repo_url": "git@github.com:brianlan/demo2.git"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["repo_url"] == "git@github.com:brianlan/demo2.git"

    delete_resp = client.delete(f"/api/v1/projects/{project_id}")
    assert delete_resp.status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404


def test_project_create_defaults(client: TestClient, registry_ids: dict[str, int]) -> None:
    payload = {
        "name": "demo",
        "repo_url": "git@github.com:brianlan/demo.git",
        "reviewer_config": _reviewer_config(registry_ids),
        "implementor_config": _implementor_config(registry_ids),
    }
    resp = client.post("/api/v1/projects", json=payload).json()
    assert resp["reviewer_config"]["trigger_interval_minutes"] == 15
    assert resp["reviewer_config"]["reviewer_cap"] == 1
    assert resp["implementor_config"]["trigger_interval_minutes"] == 15


def test_project_create_missing_repo_url(client: TestClient) -> None:
    resp = client.post("/api/v1/projects", json={"name": "demo"})
    assert resp.status_code == 422


def test_project_create_duplicate_name(client: TestClient, registry_ids: dict[str, int]) -> None:
    payload = {
        "name": "demo",
        "repo_url": "git@github.com:brianlan/demo.git",
        "reviewer_config": _reviewer_config(registry_ids),
        "implementor_config": _implementor_config(registry_ids),
    }
    assert client.post("/api/v1/projects", json=payload).status_code == 201
    assert client.post("/api/v1/projects", json=payload).status_code == 409


def test_project_create_invalid_reference(client: TestClient, registry_ids: dict[str, int]) -> None:
    payload = {
        "name": "demo",
        "repo_url": "git@github.com:brianlan/demo.git",
        "reviewer_config": _reviewer_config(registry_ids, agent_definition_id=9999),
        "implementor_config": _implementor_config(registry_ids),
    }
    resp = client.post("/api/v1/projects", json=payload)
    assert resp.status_code == 422


def test_project_update_cadence_persisted(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    payload = {
        "name": "demo",
        "repo_url": "git@github.com:brianlan/demo.git",
        "reviewer_config": _reviewer_config(registry_ids),
        "implementor_config": _implementor_config(registry_ids),
    }
    project_id = client.post("/api/v1/projects", json=payload).json()["id"]

    patch_resp = client.patch(
        f"/api/v1/projects/{project_id}",
        json={
            "reviewer_config": _reviewer_config(
                registry_ids, trigger_interval_minutes=30, reviewer_cap=3
            )
        },
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["reviewer_config"]["trigger_interval_minutes"] == 30
    assert updated["reviewer_config"]["reviewer_cap"] == 3


def test_project_update_does_not_mutate_running_snapshot(
    client: TestClient, registry_ids: dict[str, int]
) -> None:
    payload = {
        "name": "demo",
        "repo_url": "git@github.com:brianlan/demo.git",
        "reviewer_config": _reviewer_config(registry_ids),
        "implementor_config": _implementor_config(registry_ids),
    }
    project_id = client.post("/api/v1/projects", json=payload).json()["id"]

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        instance = agent_instance_create(conn, project_id, "reviewer")
        original_snapshot = {
            "agent_definition_id": registry_ids["agent_definition_id"],
            "model_entry_id": registry_ids["model_entry_id"],
            "docker_image_id": registry_ids["docker_image_id"],
        }
        config_snapshot_create(conn, instance.id, original_snapshot)
        conn.commit()

    patch_resp = client.patch(
        f"/api/v1/projects/{project_id}",
        json={
            "reviewer_config": _reviewer_config(
                registry_ids,
                agent_definition_id=registry_ids["agent_definition_id"],
                model_entry_id=registry_ids["model_entry_id"],
                docker_image_id=registry_ids["docker_image_id"],
                trigger_interval_minutes=60,
                reviewer_cap=5,
            )
        },
    )
    assert patch_resp.status_code == 200

    with db.connect() as conn:
        snapshot = config_snapshot_get(conn, instance.id)
    assert snapshot == original_snapshot
