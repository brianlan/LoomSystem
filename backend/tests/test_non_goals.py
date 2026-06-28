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
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/token",
    "/api/v1/auth/register",
    "/api/v1/auth/me",
    "/api/v1/users",
    "/api/v1/users/1",
]

WEBHOOK_PATHS = [
    "/api/v1/webhooks",
    "/api/v1/webhooks/github",
    "/api/v1/webhooks/github/events",
]

RESOURCE_CAP_PATHS = [
    "/api/v1/resource-caps",
    "/api/v1/resource-limits",
    "/api/v1/quotas",
    "/api/v1/projects/1/resource-caps",
]

CLOUD_SDK_PATHS = [
    "/api/v1/deployments",
    "/api/v1/deploy",
    "/api/v1/sdk",
    "/api/v1/api-keys",
    "/api/v1/integrations",
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
