from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "api_test.db"
    app.state.db.db_path = db_path
    get_db(db_path).reset()
    return TestClient(app)


def test_agent_definition_crud(client: TestClient) -> None:
    payload = {
        "name": "reviewer",
        "prompt_markdown": "# Reviewer",
        "github_identity": "reviewer-bot",
        "permissions": {"repo": True},
    }
    create_resp = client.post("/api/v1/settings/agent-definitions", json=payload)
    assert create_resp.status_code == 201
    data = create_resp.json()
    assert data["name"] == "reviewer"
    definition_id = data["id"]

    list_resp = client.get("/api/v1/settings/agent-definitions")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    get_resp = client.get(f"/api/v1/settings/agent-definitions/{definition_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "reviewer"

    # Duplicate name rejected.
    dup = client.post("/api/v1/settings/agent-definitions", json=payload)
    assert dup.status_code == 409

    delete_resp = client.delete(f"/api/v1/settings/agent-definitions/{definition_id}")
    assert delete_resp.status_code == 200
    assert client.get(f"/api/v1/settings/agent-definitions/{definition_id}").status_code == 404


def test_model_entry_secrets_redacted(client: TestClient) -> None:
    payload = {
        "provider_id": "anthropic",
        "model_id": "claude-3-5-sonnet",
        "credentials": "sk-secret",
        "display_name": "Claude",
    }
    create_resp = client.post("/api/v1/settings/model-entries", json=payload)
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    get_resp = client.get(f"/api/v1/settings/model-entries/{entry_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert "credentials" not in data
    assert data["display_name"] == "Claude"


def test_model_entry_update(client: TestClient) -> None:
    payload = {
        "provider_id": "anthropic",
        "model_id": "claude-3-5-sonnet",
        "credentials": "sk-secret",
    }
    entry_id = client.post("/api/v1/settings/model-entries", json=payload).json()["id"]

    patch_resp = client.patch(
        f"/api/v1/settings/model-entries/{entry_id}",
        json={"display_name": "Updated"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "Updated"


def test_docker_image_crud(client: TestClient) -> None:
    payload = {"image_name": "loomsystem/opencode-runtime:latest"}
    create_resp = client.post("/api/v1/settings/docker-images", json=payload)
    assert create_resp.status_code == 201
    image_id = create_resp.json()["id"]

    list_resp = client.get("/api/v1/settings/docker-images")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    delete_resp = client.delete(f"/api/v1/settings/docker-images/{image_id}")
    assert delete_resp.status_code == 200


def test_ssh_key_redacted_and_cannot_delete_when_project_exists(
    client: TestClient,
) -> None:
    client.put("/api/v1/settings/ssh-key", json={"key_value": "ssh-rsa AAAA..."})
    get_resp = client.get("/api/v1/settings/ssh-key")
    assert get_resp.status_code == 200
    assert get_resp.json()["key_value"] == "***"

    # Create a project so the key is considered in use.
    from app.db import get_db
    from app.repositories import project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        conn.commit()

    delete_resp = client.delete("/api/v1/settings/ssh-key")
    assert delete_resp.status_code == 409


def test_github_token_singleton(client: TestClient) -> None:
    put_resp = client.put(
        "/api/v1/settings/github-token", json={"token_value": "ghp-secret"}
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/api/v1/settings/github-token")
    assert get_resp.status_code == 200
    assert get_resp.json()["token_value"] == "***"


def test_triage_config_round_trip(client: TestClient) -> None:
    payload = {
        "endpoint_url": "https://api.openai.com/v1",
        "model_name": "gpt-4",
        "api_key": "sk-secret",
        "headers": {"Custom-Header": "value"},
    }
    put_resp = client.put("/api/v1/settings/triage-config", json=payload)
    assert put_resp.status_code == 200

    get_resp = client.get("/api/v1/settings/triage-config")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["endpoint_url"] == payload["endpoint_url"]
    assert data["model_name"] == payload["model_name"]
    assert "api_key" not in data
    assert data["headers"] == payload["headers"]


def test_proxy_round_trip(client: TestClient) -> None:
    payload = {"http_proxy": "http://proxy:8080", "https_proxy": "https://proxy:8080"}
    put_resp = client.put("/api/v1/settings/proxy", json=payload)
    assert put_resp.status_code == 200

    get_resp = client.get("/api/v1/settings/proxy")
    assert get_resp.status_code == 200
    assert get_resp.json() == payload


def test_in_use_agent_definition_deletion_blocked(client: TestClient) -> None:
    definition = client.post(
        "/api/v1/settings/agent-definitions",
        json={
            "name": "reviewer",
            "prompt_markdown": "# Reviewer",
            "github_identity": "bot",
        },
    ).json()

    from app.db import get_db
    from app.repositories import agent_instance_create, project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project = project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        agent_instance_create(
            conn,
            project_id=project.id,
            agent_type="reviewer",
            agent_definition_id=definition["id"],
        )

    delete_resp = client.delete(f"/api/v1/settings/agent-definitions/{definition['id']}")
    assert delete_resp.status_code == 409


def test_in_use_model_entry_deletion_blocked(client: TestClient) -> None:
    entry = client.post(
        "/api/v1/settings/model-entries",
        json={
            "provider_id": "anthropic",
            "model_id": "claude",
            "credentials": "sk-secret",
        },
    ).json()

    from app.db import get_db
    from app.repositories import agent_instance_create, project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project = project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        agent_instance_create(
            conn,
            project_id=project.id,
            agent_type="reviewer",
            model_entry_id=entry["id"],
        )

    delete_resp = client.delete(f"/api/v1/settings/model-entries/{entry['id']}")
    assert delete_resp.status_code == 409


def test_in_use_docker_image_deletion_blocked(client: TestClient) -> None:
    image = client.post(
        "/api/v1/settings/docker-images",
        json={"image_name": "loomsystem/runtime:latest"},
    ).json()

    from app.db import get_db
    from app.repositories import agent_instance_create, project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project = project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        agent_instance_create(
            conn,
            project_id=project.id,
            agent_type="reviewer",
            docker_image_id=image["id"],
        )

    delete_resp = client.delete(f"/api/v1/settings/docker-images/{image['id']}")
    assert delete_resp.status_code == 409


def test_agent_definition_name_uniqueness(client: TestClient) -> None:
    payload = {
        "name": "reviewer",
        "prompt_markdown": "# Reviewer",
        "github_identity": "bot",
    }
    assert client.post("/api/v1/settings/agent-definitions", json=payload).status_code == 201
    assert client.post("/api/v1/settings/agent-definitions", json=payload).status_code == 409


def test_docker_image_name_uniqueness(client: TestClient) -> None:
    payload = {"image_name": "loomsystem/runtime:latest"}
    assert client.post("/api/v1/settings/docker-images", json=payload).status_code == 201
    assert client.post("/api/v1/settings/docker-images", json=payload).status_code == 409


def test_docker_image_update(client: TestClient) -> None:
    image_id = client.post(
        "/api/v1/settings/docker-images",
        json={"image_name": "loomsystem/runtime:v1"},
    ).json()["id"]

    patch_resp = client.patch(
        f"/api/v1/settings/docker-images/{image_id}",
        json={"image_name": "loomsystem/runtime:v2"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["image_name"] == "loomsystem/runtime:v2"


def test_model_entry_credentials_redacted_in_list(client: TestClient) -> None:
    client.post(
        "/api/v1/settings/model-entries",
        json={
            "provider_id": "anthropic",
            "model_id": "claude",
            "credentials": "sk-secret",
            "display_name": "Claude",
        },
    )
    list_resp = client.get("/api/v1/settings/model-entries")
    assert list_resp.status_code == 200
    for entry in list_resp.json():
        assert "credentials" not in entry


def test_invalid_payload_returns_validation_error(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/settings/agent-definitions",
        json={"name": "missing fields"},
    )
    assert resp.status_code == 422


def test_agent_definition_in_use_blocked_by_project_config(client: TestClient) -> None:
    definition = client.post(
        "/api/v1/settings/agent-definitions",
        json={
            "name": "reviewer",
            "prompt_markdown": "# Reviewer",
            "github_identity": "bot",
        },
    ).json()

    from app.db import get_db
    from app.repositories import project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        conn.execute(
            "UPDATE projects SET reviewer_config_json = ? WHERE name = ?",
            (f'{{"agent_definition_id": {definition["id"]}}}', "demo"),
        )

    delete_resp = client.delete(f"/api/v1/settings/agent-definitions/{definition['id']}")
    assert delete_resp.status_code == 409


def test_model_entry_in_use_blocked_by_project_config(client: TestClient) -> None:
    entry = client.post(
        "/api/v1/settings/model-entries",
        json={
            "provider_id": "anthropic",
            "model_id": "claude",
            "credentials": "sk-secret",
        },
    ).json()

    from app.db import get_db
    from app.repositories import project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        conn.execute(
            "UPDATE projects SET reviewer_config_json = ? WHERE name = ?",
            (f'{{"model_entry_id": {entry["id"]}}}', "demo"),
        )

    delete_resp = client.delete(f"/api/v1/settings/model-entries/{entry['id']}")
    assert delete_resp.status_code == 409


def test_docker_image_in_use_blocked_by_project_config(client: TestClient) -> None:
    image = client.post(
        "/api/v1/settings/docker-images",
        json={"image_name": "loomsystem/runtime:latest"},
    ).json()

    from app.db import get_db
    from app.repositories import project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        conn.execute(
            "UPDATE projects SET implementor_config_json = ? WHERE name = ?",
            (f'{{"docker_image_id": {image["id"]}}}', "demo"),
        )

    delete_resp = client.delete(f"/api/v1/settings/docker-images/{image['id']}")
    assert delete_resp.status_code == 409


def test_model_entry_in_use_blocked_by_project_config(client: TestClient) -> None:
    entry = client.post(
        "/api/v1/settings/model-entries",
        json={
            "provider_id": "anthropic",
            "model_id": "claude",
            "credentials": "sk-secret",
        },
    ).json()

    from app.db import get_db
    from app.repositories import project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        conn.execute(
            "UPDATE projects SET implementor_config_json = ? WHERE name = ?",
            (f'{{"model_entry_id": {entry["id"]}}}', "demo"),
        )

    delete_resp = client.delete(f"/api/v1/settings/model-entries/{entry['id']}")
    assert delete_resp.status_code == 409


def test_docker_image_in_use_blocked_by_project_config(client: TestClient) -> None:
    image = client.post(
        "/api/v1/settings/docker-images",
        json={"image_name": "loomsystem/runtime:latest"},
    ).json()

    from app.db import get_db
    from app.repositories import project_create

    db = get_db(app.state.db.db_path)
    with db.connect() as conn:
        project_create(conn, "demo", "git@github.com:brianlan/demo.git")
        conn.execute(
            "UPDATE projects SET reviewer_config_json = ? WHERE name = ?",
            (f'{{"docker_image_id": {image["id"]}}}', "demo"),
        )

    delete_resp = client.delete(f"/api/v1/settings/docker-images/{image['id']}")
    assert delete_resp.status_code == 409
