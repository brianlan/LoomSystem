from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import repositories as repos
from app.db import get_db
from app.docker import FakeDockerAdapter, LaunchError
from app.launch_service import LaunchService, _container_labels, _container_name
from app.main import app
from app.repositories import (
    agent_definition_create,
    config_snapshot_get,
    docker_image_create,
    model_entry_create,
    project_create,
    setting_set,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "launch_test.db"
    app.state.db.db_path = db_path
    get_db(db_path).reset()
    return TestClient(app)


@pytest.fixture
def registry(client: TestClient) -> dict:
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        setting_set(
            conn,
            "ssh_key",
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "test\n"
            "-----END OPENSSH PRIVATE KEY-----\n",
        )
        agent = agent_definition_create(conn, "reviewer", "# Reviewer prompt", "bot")
        model = model_entry_create(conn, "anthropic", "claude", "sk-secret")
        image = docker_image_create(conn, "loomsystem/runtime:latest")
        project = project_create(
            conn,
            name="demo",
            repo_url="git@github.com:brianlan/demo.git",
        )
        conn.commit()
        return {
            "project_id": project.id,
            "repo_url": project.repo_url,
            "agent_definition_id": agent.id,
            "model_entry_id": model.id,
            "docker_image_id": image.id,
            "image_name": image.image_name,
        }


def _service(tmp_path: Path) -> tuple[LaunchService, FakeDockerAdapter]:
    adapter = FakeDockerAdapter()
    service = LaunchService(adapter, workspace_root=tmp_path / "work")
    return service, adapter


def test_container_name_and_labels() -> None:
    assert _container_name(1, "reviewer", 2) == "loom-1-reviewer-2"
    labels = _container_labels(1, "reviewer", 2)
    assert labels["loomsystem.managed"] == "true"
    assert labels["loomsystem.project_id"] == "1"
    assert labels["loomsystem.agent_type"] == "reviewer"
    assert labels["loomsystem.agent_instance_id"] == "2"


def test_launch_happy_path(client: TestClient, registry: dict, tmp_path: Path) -> None:
    service, adapter = _service(tmp_path)
    adapter.mark_image_present(registry["image_name"])

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        instance = service.launch(
            conn,
            project_id=registry["project_id"],
            agent_type="reviewer",
            agent_definition_id=registry["agent_definition_id"],
            model_entry_id=registry["model_entry_id"],
            docker_image_id=registry["docker_image_id"],
            issue_number=42,
        )

    assert instance.status == "running"
    assert instance.container_id is not None
    container = adapter.containers[instance.container_id]
    assert container["name"] == _container_name(
        registry["project_id"], "reviewer", instance.id
    )
    assert container["labels"] == _container_labels(
        registry["project_id"], "reviewer", instance.id
    )
    assert container["env"]["LOOM_AGENT_TYPE"] == "reviewer"
    assert container["env"]["LOOM_ISSUE_NUMBER"] == "42"
    assert container["env"]["LOOM_MODEL_CREDENTIALS"] == "sk-secret"

    workspace = tmp_path / "work" / "launches" / str(instance.id)
    assert (workspace / "agent.md").read_text() == "# Reviewer prompt"
    assert (workspace / "ssh_key").stat().st_mode & 0o777 == 0o600
    assert (workspace / "provider_config.json").read_text() == "{}"

    with db.connect() as conn:
        snapshot = config_snapshot_get(conn, instance.id)
    assert snapshot is not None
    assert snapshot["agent_type"] == "reviewer"
    assert snapshot["docker_image_name"] == registry["image_name"]


def test_launch_auto_pulls_missing_image(
    client: TestClient, registry: dict, tmp_path: Path
) -> None:
    service, adapter = _service(tmp_path)
    assert not adapter.image_exists(registry["image_name"])

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        service.launch(
            conn,
            project_id=registry["project_id"],
            agent_type="reviewer",
            agent_definition_id=registry["agent_definition_id"],
            model_entry_id=registry["model_entry_id"],
            docker_image_id=registry["docker_image_id"],
        )

    assert any(call[0] == "pull" for call in adapter.calls)


def test_launch_pull_failure(client: TestClient, registry: dict, tmp_path: Path) -> None:
    service, adapter = _service(tmp_path)
    adapter.set_pull_fail()

    db = get_db(app.state.db.db_path)
    with pytest.raises(LaunchError, match="Failed to pull image"):
        with db.connect() as conn:
            service.launch(
                conn,
                project_id=registry["project_id"],
                agent_type="reviewer",
                agent_definition_id=registry["agent_definition_id"],
                model_entry_id=registry["model_entry_id"],
                docker_image_id=registry["docker_image_id"],
            )

    with db.connect() as conn:
        instances = [
            repos.agent_instance_get(conn, row["id"])
            for row in conn.execute("SELECT id FROM agent_instances").fetchall()
        ]
    assert instances
    assert instances[0] is not None
    assert instances[0].status == "error"


def test_launch_ssh_clone_failure(client: TestClient, registry: dict, tmp_path: Path) -> None:
    service, adapter = _service(tmp_path)
    adapter.mark_image_present(registry["image_name"])
    adapter.set_default_exec_result(
        ["git", "clone", "--depth", "1", registry["repo_url"], "/loom/repo"],
        128,
        "permission denied",
    )

    db = get_db(app.state.db.db_path)
    with pytest.raises(LaunchError, match="SSH clone failed"):
        with db.connect() as conn:
            service.launch(
                conn,
                project_id=registry["project_id"],
                agent_type="reviewer",
                agent_definition_id=registry["agent_definition_id"],
                model_entry_id=registry["model_entry_id"],
                docker_image_id=registry["docker_image_id"],
            )


def test_launch_model_credential_failure(
    client: TestClient, registry: dict, tmp_path: Path
) -> None:
    service, adapter = _service(tmp_path)
    adapter.mark_image_present(registry["image_name"])
    adapter.set_default_exec_result(
        ["sh", "-c", 'test -n "$LOOM_MODEL_CREDENTIALS"'],
        1,
        "missing",
    )

    # Force empty credentials by updating the model entry.
    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        conn.execute(
            "UPDATE model_entries SET credentials = ? WHERE id = ?",
            ("", registry["model_entry_id"]),
        )

    with pytest.raises(LaunchError, match="Model credentials not reachable"):
        with db.connect() as conn:
            service.launch(
                conn,
                project_id=registry["project_id"],
                agent_type="reviewer",
                agent_definition_id=registry["agent_definition_id"],
                model_entry_id=registry["model_entry_id"],
                docker_image_id=registry["docker_image_id"],
            )


def test_proxy_env_vars_only_inside_container(
    client: TestClient, registry: dict, tmp_path: Path
) -> None:
    service, adapter = _service(tmp_path)
    adapter.mark_image_present(registry["image_name"])

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        setting_set(conn, "http_proxy", "http://proxy.example:8080")
        setting_set(conn, "https_proxy", "https://proxy.example:8443")
        instance = service.launch(
            conn,
            project_id=registry["project_id"],
            agent_type="implementor",
            agent_definition_id=registry["agent_definition_id"],
            model_entry_id=registry["model_entry_id"],
            docker_image_id=registry["docker_image_id"],
        )

    assert instance.container_id is not None
    env = adapter.containers[instance.container_id]["env"]
    assert env["http_proxy"] == "http://proxy.example:8080"
    assert env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert env["https_proxy"] == "https://proxy.example:8443"
    assert env["HTTPS_PROXY"] == "https://proxy.example:8443"


def test_no_proxy_env_without_settings(client: TestClient, registry: dict, tmp_path: Path) -> None:
    service, adapter = _service(tmp_path)
    adapter.mark_image_present(registry["image_name"])

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        instance = service.launch(
            conn,
            project_id=registry["project_id"],
            agent_type="reviewer",
            agent_definition_id=registry["agent_definition_id"],
            model_entry_id=registry["model_entry_id"],
            docker_image_id=registry["docker_image_id"],
        )

    assert instance.container_id is not None
    env = adapter.containers[instance.container_id]["env"]
    assert "http_proxy" not in env
    assert "HTTP_PROXY" not in env


def test_error_message_does_not_leak_credentials(
    client: TestClient, registry: dict, tmp_path: Path
) -> None:
    service, adapter = _service(tmp_path)
    adapter.mark_image_present(registry["image_name"])
    adapter.set_run_fail()

    db = get_db(app.state.db.db_path)
    with pytest.raises(LaunchError) as exc_info:
        with db.connect() as conn:
            service.launch(
                conn,
                project_id=registry["project_id"],
                agent_type="reviewer",
                agent_definition_id=registry["agent_definition_id"],
                model_entry_id=registry["model_entry_id"],
                docker_image_id=registry["docker_image_id"],
            )

    assert "sk-secret" not in str(exc_info.value)
