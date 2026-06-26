"""Tests for the opencode trigger execution service (T07).

Covers: scheduler minimum-gap (15-min default + configurable interval),
non-overlap, first-trigger session-id capture, session reuse on subsequent
triggers, non-zero exit recorded as a failed trigger (no container restart),
incomplete-stream recording, and manual trigger dispatch.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app import repositories as repos
from app.db import get_db
from app.docker import FakeDockerAdapter
from app.trigger import (
    TriggerError,
    TriggerRequest,
    TriggerScheduler,
    TriggerService,
    build_trigger_command,
    extract_session_id,
    trigger_request_for_instance,
)

# Default trigger interval (FR-4, D46).
DEFAULT_INTERVAL_MINUTES = 15


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "trigger_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


def _seed_instance(
    conn: sqlite3.Connection,
    *,
    container_id: str = "cid-reviewer",
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
) -> tuple[repos.AgentInstance, TriggerRequest]:
    """Create a reviewer agent instance + config snapshot; return instance + request."""
    repos.agent_definition_create(conn, "reviewer", "# Reviewer prompt", "bot")
    repos.model_entry_create(conn, "anthropic", "claude-3", "sk-secret")
    repos.docker_image_create(conn, "loomsystem/runtime:latest")
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    instance = repos.agent_instance_create(
        conn,
        project_id=project.id,
        agent_type="reviewer",
        agent_definition_id=1,
        model_entry_id=1,
        docker_image_id=1,
        container_id=container_id,
        container_name="loom-reviewer",
    )
    repos.config_snapshot_create(
        conn,
        instance.id,
        {
            "model_provider_id": "anthropic",
            "model_id": "claude-3",
            "agent_name": "reviewer",
        },
    )
    req = TriggerRequest(
        agent_instance_id=instance.id,
        project_id=project.id,
        container_id=container_id,
        model="anthropic/claude-3",
        agent_name="reviewer",
        prompt="Review the latest changes",
        interval_minutes=interval_minutes,
    )
    return instance, req


# ---------------------------------------------------------------------------
# extract_session_id / build_trigger_command
# ---------------------------------------------------------------------------


def test_extract_session_id_from_json_event() -> None:
    line = '{"event":"session.start","sessionID":"sess-abc12345xyz"}'
    assert extract_session_id(line) == "sess-abc12345xyz"


def test_extract_session_id_json_snake_case() -> None:
    assert extract_session_id('{"session_id":"01HZX"}') == "01HZX"


def test_extract_session_id_text_fallback() -> None:
    assert extract_session_id("started session sess-abcdef12") == "sess-abcdef12"


def test_extract_session_id_none_when_absent() -> None:
    assert extract_session_id("just some agent output") is None


def test_build_trigger_command_first_run_has_no_session() -> None:
    cmd = build_trigger_command("anthropic/claude-3", "reviewer", "do review", None)
    assert cmd == [
        "opencode", "run", "-m", "anthropic/claude-3",
        "--agent", "reviewer", "do review",
    ]
    assert "--session" not in cmd


def test_build_trigger_command_subsequent_run_reuses_session() -> None:
    cmd = build_trigger_command("anthropic/claude-3", "reviewer", "do review", "sess-1")
    assert "--session" in cmd
    idx = cmd.index("--session")
    assert cmd[idx + 1] == "sess-1"


# ---------------------------------------------------------------------------
# TriggerScheduler: minimum-gap + non-overlap
# ---------------------------------------------------------------------------


def test_scheduler_first_trigger_is_due() -> None:
    t = [0.0]
    sched = TriggerScheduler(clock=lambda: t[0])
    assert sched.is_due(1, DEFAULT_INTERVAL_MINUTES) is True


def test_scheduler_default_15_min_gap_not_elapsed() -> None:
    t = [0.0]
    sched = TriggerScheduler(clock=lambda: t[0])
    assert sched.try_acquire(1, DEFAULT_INTERVAL_MINUTES) is True  # first fire
    sched.release(1)
    # 14 minutes later -> not due
    t[0] = 14 * 60
    assert sched.is_due(1, DEFAULT_INTERVAL_MINUTES) is False


def test_scheduler_default_15_min_gap_elapsed() -> None:
    t = [0.0]
    sched = TriggerScheduler(clock=lambda: t[0])
    assert sched.try_acquire(1, DEFAULT_INTERVAL_MINUTES) is True
    sched.release(1)
    t[0] = 15 * 60
    assert sched.is_due(1, DEFAULT_INTERVAL_MINUTES) is True


def test_scheduler_configurable_interval() -> None:
    t = [0.0]
    sched = TriggerScheduler(clock=lambda: t[0])
    assert sched.try_acquire(1, 5) is True
    sched.release(1)
    t[0] = 4 * 60
    assert sched.is_due(1, 5) is False
    t[0] = 5 * 60
    assert sched.is_due(1, 5) is True


def test_scheduler_prevents_overlap_while_in_progress() -> None:
    t = [0.0]
    sched = TriggerScheduler(clock=lambda: t[0])
    assert sched.try_acquire(1, DEFAULT_INTERVAL_MINUTES) is True
    # While in flight, a second acquire is refused even though time advanced 0.
    assert sched.try_acquire(1, DEFAULT_INTERVAL_MINUTES) is False
    assert sched.is_due(1, DEFAULT_INTERVAL_MINUTES) is False
    sched.release(1)
    # After release (no time advance) still not due because gap not elapsed.
    assert sched.is_due(1, DEFAULT_INTERVAL_MINUTES) is False


# ---------------------------------------------------------------------------
# TriggerService: session capture, reuse, exit codes, incomplete, manual
# ---------------------------------------------------------------------------


def test_first_trigger_captures_and_persists_session_id(conn: sqlite3.Connection) -> None:
    instance, req = _seed_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (
        ['{"event":"session.start","sessionID":"sess-abc12345"}\n'],
        0,
    )
    service = TriggerService(adapter)

    outcome = service.run(conn, req)

    assert outcome.exit_code == 0
    assert outcome.incomplete is False
    assert outcome.captured_session_id == "sess-abc12345"
    # Session id persisted on the agent instance (FR-16g).
    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.session_id == "sess-abc12345"


def test_subsequent_trigger_reuses_session_id(conn: sqlite3.Connection) -> None:
    instance, req = _seed_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (
        ['{"event":"session.start","sessionID":"sess-reuse-99"}\n'],
        0,
    )
    clock = [0.0]
    service = TriggerService(adapter, TriggerScheduler(clock=lambda: clock[0]))

    service.run(conn, req)  # first trigger captures session

    # Advance the clock past the gap so the second trigger is due.
    clock[0] = 16 * 60
    outcome = service.run(conn, req)

    # Second invocation must carry --session with the captured id.
    second_cmd = adapter.exec_stream_calls[1][1]
    assert "--session" in second_cmd
    idx = second_cmd.index("--session")
    assert second_cmd[idx + 1] == "sess-reuse-99"
    # No new session captured on a subsequent trigger.
    assert outcome.captured_session_id is None
    refreshed = repos.agent_instance_get(conn, instance.id)
    assert refreshed is not None
    assert refreshed.session_id == "sess-reuse-99"


def test_non_due_trigger_raises(conn: sqlite3.Connection) -> None:
    _instance, req = _seed_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (["ok\n"], 0)
    service = TriggerService(adapter, TriggerScheduler(clock=lambda: 0.0))

    service.run(conn, req)  # first fire
    # Same instant -> not due.
    with pytest.raises(TriggerError, match="minimum gap not elapsed"):
        service.run(conn, req)


def test_non_zero_exit_recorded_as_failed_trigger_not_container_failure(
    conn: sqlite3.Connection,
) -> None:
    """EC-3: opencode non-zero exit is a failed trigger; container is untouched."""
    instance, req = _seed_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (["error: model refused\n"], 2)
    service = TriggerService(adapter)

    outcome = service.run(conn, req)

    assert outcome.exit_code == 2
    assert outcome.incomplete is False
    # Container was NOT stopped/removed (no restart counter increment).
    assert len(adapter.containers) == 0
    # Latest trigger record reflects a failed (non-zero) trigger.
    trigger = repos.trigger_latest_for_instance(conn, instance.id)
    assert trigger is not None
    assert trigger.exit_code == 2
    assert trigger.ended_at is not None
    assert "model refused" in (trigger.output or "")


def test_incomplete_stream_recorded_when_exit_code_lost(conn: sqlite3.Connection) -> None:
    """EC-1/EC-2: a stream that ends without a clean exit is recorded incomplete."""
    instance, req = _seed_instance(conn)
    adapter = FakeDockerAdapter()
    # exit_code None models a lost stream.
    adapter.exec_stream_default = (["partial output...\n"], None)
    service = TriggerService(adapter)

    outcome = service.run(conn, req)

    assert outcome.incomplete is True
    assert outcome.exit_code is None
    trigger = repos.trigger_latest_for_instance(conn, instance.id)
    assert trigger is not None
    assert trigger.exit_code is None  # NULL = incomplete
    assert trigger.ended_at is not None
    assert "partial output" in (trigger.output or "")


def test_manual_trigger_bypasses_minimum_gap(conn: sqlite3.Connection) -> None:
    """FR-19/D38: manual trigger fires immediately regardless of the gap."""
    _instance, req = _seed_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (["manual run\n"], 0)
    service = TriggerService(adapter, TriggerScheduler(clock=lambda: 0.0))

    service.run(conn, req)
    # Manual trigger at the same instant succeeds.
    outcome = service.run(conn, req, manual=True)
    assert outcome.exit_code == 0
    assert len(adapter.exec_stream_calls) == 2


def test_manual_trigger_still_blocked_by_overlap(conn: sqlite3.Connection) -> None:
    """Even a manual trigger must not overlap an in-flight trigger."""
    _instance, req = _seed_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (["run\n"], 0)
    # Simulate an in-flight trigger manually acquiring the slot.
    sched = TriggerScheduler(clock=lambda: 0.0)
    assert sched.try_acquire(req.agent_instance_id, DEFAULT_INTERVAL_MINUTES) is True
    service = TriggerService(adapter, sched)
    with pytest.raises(TriggerError, match="already in progress"):
        service.run(conn, req, manual=True)


def test_trigger_records_audit_event(conn: sqlite3.Connection) -> None:
    """OBS-1: every trigger is recorded in the audit trail."""
    instance, req = _seed_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (["ok\n"], 0)
    service = TriggerService(adapter)

    service.run(conn, req)

    events = repos.audit_event_list(conn, agent_instance_id=instance.id)
    assert len(events) == 1
    assert events[0]["event_type"] == "trigger"
    assert events[0]["payload"]["exit_code"] == 0


def test_trigger_request_for_instance_builds_from_snapshot(
    conn: sqlite3.Connection,
) -> None:
    instance, _req = _seed_instance(conn)
    req = trigger_request_for_instance(
        conn, instance.id, "Review changes", DEFAULT_INTERVAL_MINUTES
    )
    assert req is not None
    assert req.agent_instance_id == instance.id
    assert req.model == "anthropic/claude-3"
    assert req.agent_name == "reviewer"
    assert req.container_id == "cid-reviewer"
    assert req.prompt == "Review changes"


def test_trigger_request_for_instance_none_when_missing(conn: sqlite3.Connection) -> None:
    assert trigger_request_for_instance(conn, 999, "p", 15) is None
