import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from app.db import get_db
from app.repositories import (
    AgentDefinition,
    Project,
    agent_definition_create,
    agent_definition_get,
    agent_definition_list,
    agent_instance_create,
    agent_instance_get,
    agent_instance_update,
    audit_event_create,
    audit_event_list,
    config_snapshot_create,
    config_snapshot_get,
    console_chunk_append,
    console_chunk_list,
    docker_image_create,
    docker_image_get,
    model_entry_create,
    model_entry_get,
    notification_create,
    notification_list,
    notification_mark_read,
    project_create,
    project_delete,
    project_get,
    project_list,
    project_update_config,
    setting_get,
    setting_set,
    triage_run_create,
    triage_run_latest,
    trigger_create,
    trigger_finish,
)


@pytest.fixture
def conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    db = get_db(tmp_path / "test.db")
    with db.connect() as c:
        yield c


@pytest.fixture
def sample_project(conn: sqlite3.Connection) -> Project:
    return project_create(conn, "demo", "git@github.com:brianlan/demo.git")


@pytest.fixture
def sample_agent(conn: sqlite3.Connection) -> AgentDefinition:
    return agent_definition_create(
        conn,
        name="reviewer",
        prompt_markdown="# Reviewer",
        github_identity="reviewer-bot",
        permissions={"repo": True},
    )


def test_setting_round_trip(conn: sqlite3.Connection) -> None:
    assert setting_get(conn, "foo") is None
    setting_set(conn, "foo", "bar")
    assert setting_get(conn, "foo") == "bar"
    setting_set(conn, "foo", "baz")
    assert setting_get(conn, "foo") == "baz"


def test_project_crud(conn: sqlite3.Connection, sample_project: Project) -> None:
    fetched = project_get(conn, sample_project.id)
    assert fetched is not None
    assert fetched.name == "demo"
    assert fetched.repo_url == "git@github.com:brianlan/demo.git"

    project_update_config(conn, sample_project.id, reviewer_config={"interval": 15})
    fetched = project_get(conn, sample_project.id)
    assert fetched is not None
    assert fetched.reviewer_config == {"interval": 15}

    assert len(project_list(conn)) == 1
    project_delete(conn, sample_project.id)
    assert project_get(conn, sample_project.id) is None


def test_agent_definition_crud(conn: sqlite3.Connection, sample_agent: AgentDefinition) -> None:
    fetched = agent_definition_get(conn, sample_agent.id)
    assert fetched is not None
    assert fetched.name == "reviewer"
    assert fetched.permissions == {"repo": True}
    assert len(agent_definition_list(conn)) == 1


def test_model_entry_credentials_persisted(conn: sqlite3.Connection) -> None:
    entry = model_entry_create(
        conn,
        provider_id="anthropic",
        model_id="claude-3-5-sonnet",
        credentials="sk-secret",
        display_name="Claude",
    )
    fetched = model_entry_get(conn, entry.id)
    assert fetched is not None
    assert fetched.credentials == "sk-secret"
    assert fetched.display_name == "Claude"


def test_docker_image_crud(conn: sqlite3.Connection) -> None:
    image = docker_image_create(conn, "loomsystem/opencode-runtime:latest")
    fetched = docker_image_get(conn, image.id)
    assert fetched is not None
    assert fetched.image_name == "loomsystem/opencode-runtime:latest"


def test_agent_instance_and_snapshot(
    conn: sqlite3.Connection, sample_project: Project, sample_agent: AgentDefinition
) -> None:
    instance = agent_instance_create(
        conn,
        project_id=sample_project.id,
        agent_type="reviewer",
        agent_definition_id=sample_agent.id,
        container_id="abc123",
        session_id="session-1",
    )
    fetched = agent_instance_get(conn, instance.id)
    assert fetched is not None
    assert fetched.status == "running"

    snapshot = {"agent": "reviewer", "model": "claude"}
    config_snapshot_create(conn, instance.id, snapshot)
    assert config_snapshot_get(conn, instance.id) == snapshot

    agent_instance_update(conn, instance.id, status="stopped", session_id="session-2")
    fetched = agent_instance_get(conn, instance.id)
    assert fetched is not None
    assert fetched.status == "stopped"
    assert fetched.session_id == "session-2"


def test_trigger_finish(conn: sqlite3.Connection, sample_project: Project) -> None:
    instance = agent_instance_create(conn, sample_project.id, "reviewer")
    trigger = trigger_create(conn, instance.id)
    trigger_finish(conn, trigger.id, 0, "done")
    row = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger.id,)).fetchone()
    assert row["exit_code"] == 0
    assert row["output"] == "done"
    assert row["ended_at"] is not None


def test_console_chunks_append_and_replay(
    conn: sqlite3.Connection, sample_project: Project
) -> None:
    instance = agent_instance_create(conn, sample_project.id, "reviewer")
    console_chunk_append(conn, instance.id, 0, "line 1\n")
    console_chunk_append(conn, instance.id, 1, "line 2\n")
    chunks = console_chunk_list(conn, instance.id)
    assert len(chunks) == 2
    assert chunks[0]["content"] == "line 1\n"
    assert chunks[1]["content"] == "line 2\n"


def test_console_durability_across_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "durability.db"
    db = get_db(db_path)
    with db.connect() as c:
        project = project_create(c, "demo", "git@github.com:brianlan/demo.git")
        instance = agent_instance_create(c, project.id, "reviewer")
        console_chunk_append(c, instance.id, 0, "persisted\n")

    # Reopen the same database file.
    db2 = get_db(db_path)
    with db2.connect() as c:
        chunks = console_chunk_list(c, instance.id)
    assert len(chunks) == 1
    assert chunks[0]["content"] == "persisted\n"


def test_audit_events(conn: sqlite3.Connection, sample_project: Project) -> None:
    audit_event_create(
        conn,
        event_type="agent_launched",
        project_id=sample_project.id,
        payload={"type": "reviewer"},
    )
    events = audit_event_list(conn, project_id=sample_project.id)
    assert len(events) == 1
    assert events[0]["event_type"] == "agent_launched"
    assert events[0]["payload"] == {"type": "reviewer"}


def test_triage_run_latest(conn: sqlite3.Connection, sample_project: Project) -> None:
    triage_run_create(conn, sample_project.id, [10, 5, 3])
    triage_run_create(conn, sample_project.id, [11, 6])
    assert triage_run_latest(conn, sample_project.id) == [11, 6]


def test_notifications(conn: sqlite3.Connection, sample_project: Project) -> None:
    nid = notification_create(conn, "Backlog drained", project_id=sample_project.id)
    unread = notification_list(conn, project_id=sample_project.id, unread_only=True)
    assert len(unread) == 1
    notification_mark_read(conn, nid)
    unread = notification_list(conn, project_id=sample_project.id, unread_only=True)
    assert len(unread) == 0


def test_config_snapshot_unchanged_after_settings_update(
    conn: sqlite3.Connection, sample_project: Project
) -> None:
    instance = agent_instance_create(conn, sample_project.id, "reviewer")
    snapshot = {"model": "claude-v1"}
    config_snapshot_create(conn, instance.id, snapshot)

    # Simulate a settings update that happens after launch.
    setting_set(conn, "global_model", "claude-v2")

    assert config_snapshot_get(conn, instance.id) == snapshot
