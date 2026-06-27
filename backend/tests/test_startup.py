from pathlib import Path

from fastapi.testclient import TestClient

from app.db import get_db
from app.docker import FakeDockerAdapter
from app.main import app

_IMAGE = "loomsystem/runtime:latest"


def test_lifespan_startup_succeeds(tmp_path: Path) -> None:
    db_path = tmp_path / "startup_test.db"
    app.state.db.db_path = db_path
    get_db(db_path).reset()

    adapter = FakeDockerAdapter()
    adapter.images.add(_IMAGE)
    app.state.docker_adapter = adapter

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
