from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "non_goals_test.db"
    app.state.db.db_path = db_path
    get_db(db_path).reset()
    return TestClient(app)


AUTH_PATHS = [
    "/auth/login",
    "/auth/logout",
    "/auth/token",
    "/auth/register",
    "/auth/me",
    "/users",
    "/users/1",
]

WEBHOOK_PATHS = [
    "/webhooks",
    "/webhooks/github",
    "/webhooks/github/events",
]

RESOURCE_CAP_PATHS = [
    "/resource-caps",
    "/resource-limits",
    "/quotas",
    "/projects/1/resource-caps",
]

CLOUD_SDK_PATHS = [
    "/deployments",
    "/deploy",
    "/sdk",
    "/api-keys",
    "/integrations",
]


def test_auth_routes_absent(client: TestClient) -> None:
    for path in AUTH_PATHS:
        resp = client.get(path)
        assert resp.status_code == 404, f"Expected 404 for {path}, got {resp.status_code}"


def test_webhook_routes_absent(client: TestClient) -> None:
    for path in WEBHOOK_PATHS:
        resp = client.post(path, json={})
        assert resp.status_code == 404, f"Expected 404 for {path}, got {resp.status_code}"


def test_resource_cap_routes_absent(client: TestClient) -> None:
    for path in RESOURCE_CAP_PATHS:
        resp = client.get(path)
        assert resp.status_code == 404, f"Expected 404 for {path}, got {resp.status_code}"


def test_cloud_sdk_routes_absent(client: TestClient) -> None:
    for path in CLOUD_SDK_PATHS:
        resp = client.get(path)
        assert resp.status_code == 404, f"Expected 404 for {path}, got {resp.status_code}"
