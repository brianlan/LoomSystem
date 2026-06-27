"""Tests for container monitoring, auto-restart cap, and backend recovery (T13)."""

import asyncio
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from app import repositories as repos
from app.container_monitor import (
    DEFAULT_RETRY_CAP,
    ContainerMonitor,
    check_containers,
    recover_on_startup,
    relaunch_agent,
)
from app.db import get_db
from app.docker import FakeDockerAdapter
from app.launch import LABEL_INSTANCE, LaunchSpec, launch_agent
from app.trigger import TriggerService


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "monitor_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


def _seed_registry(conn: sqlite3.Connection) -> None:
    repos.agent_definition_create(conn, "reviewer", "# Reviewer", "bot")
    repos.model_entry_create(conn, "anthropic", "claude-3", "sk-secret")
    repos.docker_image_create(conn, "loomsystem/runtime:latest")
    repos.setting_set(
        conn,
        "ssh_key",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n",
    )


def _make_project(conn: sqlite3.Connection) -> repos.Project:
    if not repos.project_list(conn):
        _seed_registry(conn)
        return repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    return repos.project_list(conn)[0]


def _make_spec(agent_type: str = "reviewer") -> LaunchSpec:
    return LaunchSpec(
        project_id=1,
        project_name="demo",
        repo_url="git@github.com:brianlan/demo.git",
        agent_type=agent_type,
        agent_definition_id=1,
        agent_name="reviewer" if agent_type == "reviewer" else "implementor",
        prompt_markdown="# Prompt",
        github_identity="bot",
        model_entry_id=1,
        model_provider_id="anthropic",
        model_id="claude-3",
        model_credentials="sk-secret",
        model_custom_config=None,
        docker_image_id=1,
        docker_image_name="loomsystem/runtime:latest",
        ssh_key="-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n",
    )


def _launch(
    conn: sqlite3.Connection, adapter: FakeDockerAdapter, agent_type: str = "reviewer"
) -> repos.AgentInstance:
    """Launch an agent and return its instance with a populated config snapshot."""
    _make_project(conn)
    adapter.images.add("loomsystem/runtime:latest")
    adapter.exec_default = (0, "anthropic/claude-3")
    result = launch_agent(conn, _make_spec(agent_type), adapter)
    instance = repos.agent_instance_get(conn, int(result.labels[LABEL_INSTANCE]))
    assert instance is not None
    return instance


def _cid(instance: repos.AgentInstance) -> str:
    """Narrow container_id to a non-None str for adapter calls."""
    assert instance.container_id is not None
    return instance.container_id


# ---------------------------------------------------------------------------
# Monitor: unexpected container death restart
# ---------------------------------------------------------------------------


def test_relaunch_agent_returns_true_and_updates_container(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)
    original_cid = _cid(instance)

    adapter.containers[original_cid]["state"] = "exited"
    assert relaunch_agent(conn, instance.id, adapter) is True

    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.container_id != original_cid


def test_monitor_restarts_dead_container(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)
    original_cid = _cid(instance)

    adapter.containers[original_cid]["state"] = "exited"
    check_containers(conn, adapter, now=time.time())

    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.container_id != original_cid
    assert refreshed.restart_count == 1
    assert refreshed.last_restart_at is not None


def test_monitor_leaves_running_container_unchanged(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)

    check_containers(conn, adapter, now=time.time())

    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.container_id == instance.container_id
    assert refreshed.restart_count == 0


# ---------------------------------------------------------------------------
# Retry cap
# ---------------------------------------------------------------------------


def _recent_timestamp(seconds_ago: int = 10) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(time.time() - seconds_ago, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_monitor_marks_failed_after_retry_cap_exceeded(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)

    # Pre-seed retry count within the window so the next death pushes it over the cap.
    repos.agent_instance_update(
        conn,
        instance.id,
        restart_count=DEFAULT_RETRY_CAP,
        last_restart_at=_recent_timestamp(),
    )

    adapter.containers[_cid(instance)]["state"] = "exited"
    check_containers(conn, adapter, now=time.time())

    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.container_id == instance.container_id


def test_monitor_resets_retry_count_outside_window(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)

    # Old retry burst outside the window should not count against the cap.
    repos.agent_instance_update(
        conn,
        instance.id,
        restart_count=DEFAULT_RETRY_CAP,
        last_restart_at="2020-01-01 00:00:00",
    )

    adapter.containers[_cid(instance)]["state"] = "exited"
    check_containers(conn, adapter, now=time.time())

    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.restart_count == 1


# ---------------------------------------------------------------------------
# Permanent failure: notification, audit, implementor re-queue
# ---------------------------------------------------------------------------


def test_permanent_failure_creates_notification_and_audit(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)

    repos.agent_instance_update(
        conn,
        instance.id,
        restart_count=DEFAULT_RETRY_CAP,
        last_restart_at=_recent_timestamp(),
    )
    adapter.containers[_cid(instance)]["state"] = "exited"

    check_containers(conn, adapter, now=time.time())

    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.status == "failed"

    notifications = repos.notification_list(conn)
    assert any("permanently failed" in n["message"] for n in notifications)

    audits = repos.audit_event_list(conn)
    assert any(a["event_type"] == "agent_failed" for a in audits)


def test_permanent_failure_requeues_implementor_issue(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    project = _make_project(conn)
    conn.execute(
        "INSERT INTO github_issues (project_id, issue_number, title, state, loom_status) "
        "VALUES (?, ?, ?, ?, ?)",
        (project.id, 42, "Fix it", "open", repos.ISSUE_STATUS_IN_PROGRESS),
    )

    instance = _launch(conn, adapter, agent_type="implementor")
    repos.agent_instance_update(conn, instance.id, issue_number=42)

    repos.agent_instance_update(
        conn,
        instance.id,
        restart_count=DEFAULT_RETRY_CAP,
        last_restart_at=_recent_timestamp(),
    )
    adapter.containers[_cid(instance)]["state"] = "exited"

    check_containers(conn, adapter, now=time.time())

    issue = repos.github_issue_get(conn, project.id, 42)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_UNASSIGNED


# ---------------------------------------------------------------------------
# Startup recovery
# ---------------------------------------------------------------------------


def test_startup_recovery_reconnects_surviving_container(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)
    trigger_service = TriggerService(adapter)

    recover_on_startup(conn, adapter, trigger_service, now=time.time())

    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.container_id == instance.container_id
    assert refreshed.status == "running"

    audits = repos.audit_event_list(conn)
    assert any(a["event_type"] == "agent_reconnected" for a in audits)


def test_startup_recovery_restarts_missing_container(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)
    adapter.remove(_cid(instance))
    trigger_service = TriggerService(adapter)

    recover_on_startup(conn, adapter, trigger_service, now=time.time())

    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.container_id is not None
    assert refreshed.restart_count == 1


def test_startup_recovery_abandoned_trigger_recorded_incomplete(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)
    trigger = repos.trigger_create(conn, instance.id)
    trigger_service = TriggerService(adapter)

    recover_on_startup(conn, adapter, trigger_service, now=time.time())

    finished = repos.trigger_latest_for_instance(conn, instance.id)
    assert finished is not None
    assert finished.id == trigger.id
    assert finished.exit_code is None
    assert "abandoned by backend restart" in (finished.output or "")


def test_startup_recovery_seeds_trigger_scheduler(conn: sqlite3.Connection) -> None:
    adapter = FakeDockerAdapter()
    instance = _launch(conn, adapter)
    trigger_service = TriggerService(adapter)

    recover_on_startup(conn, adapter, trigger_service, now=time.time())

    # Seeded agent is not immediately due (next fire = restart time + interval).
    assert not trigger_service.scheduler.is_due(instance.id, interval_minutes=15)


# ---------------------------------------------------------------------------
# Background monitor service lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_container_monitor_start_stop(db_path: Path) -> None:
    adapter = FakeDockerAdapter()
    monitor = ContainerMonitor(db_path, adapter, interval_seconds=0.01)
    monitor.start()
    await asyncio.sleep(0.02)
    monitor.stop()
    assert monitor._task is None or monitor._task.done()
