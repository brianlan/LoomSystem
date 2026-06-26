import sqlite3
import stat
from pathlib import Path

import pytest

from app.db import MIGRATIONS_DIR, Database, get_db, load_migrations


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "test.db"
    return get_db(db_path)


def test_migrations_create_expected_tables(tmp_db: Database) -> None:
    with tmp_db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    expected = {
        "schema_migrations",
        "settings",
        "projects",
        "agent_definitions",
        "model_entries",
        "docker_images",
        "agent_instances",
        "config_snapshots",
        "triggers",
        "console_chunks",
        "audit_events",
        "triage_runs",
        "notifications",
        "github_issues",
        "github_pull_requests",
        "github_polling_status",
    }
    assert expected <= tables


def test_migrations_are_idempotent(tmp_db: Database) -> None:
    # Running migrations again should not fail and should not duplicate rows.
    migrations = load_migrations(MIGRATIONS_DIR)
    tmp_db.migrate(migrations)
    with tmp_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 2


def test_database_file_is_private(tmp_path: Path) -> None:
    db_path = tmp_path / "private.db"
    get_db(db_path)
    mode = stat.S_IMODE(db_path.stat().st_mode)
    assert mode == 0o600


def test_foreign_keys_enforced(tmp_db: Database) -> None:
    with tmp_db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agent_instances (project_id, agent_type) VALUES (?, ?)",
                (999, "reviewer"),
            )
