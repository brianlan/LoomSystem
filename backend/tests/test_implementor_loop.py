"""Tests for the implementor loop orchestration service."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app import repositories as repos
from app.db import get_db
from app.github import IssueDTO
from app.implementor_loop import (
    STATE_DRAINING,
    STATE_IDLE,
    STATE_RUNNING,
    ImplementorLoopError,
    check_draining_complete,
    get_status,
    hard_stop,
    refill,
    requeue_failed_issue,
    soft_stop,
    start_loop,
)
from app.polling import poll_project


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "implementor_loop_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


def _seed_project(
    conn: sqlite3.Connection,
    issues: list[IssueDTO],
    parallelism: int = 1,
) -> repos.Project:
    """Create a project, seed triage config, and poll open issues into snapshots."""
    # Seed registry entries so FK constraints pass.
    repos.agent_definition_create(conn, "implementor", "# Implementor", "bot")
    repos.model_entry_create(conn, "anthropic", "claude-3", "sk-secret")
    repos.docker_image_create(conn, "loomsystem/runtime:latest")
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    repos.project_update(
        conn,
        project.id,
        implementor_config={
            "agent_definition_id": 1,
            "model_entry_id": 1,
            "docker_image_id": 1,
            "trigger_interval_minutes": 15,
            "parallelism": parallelism,
        },
    )
    # Seed triage config.
    repos.setting_set(conn, "triage_endpoint_url", "https://api.example.com")
    repos.setting_set(conn, "triage_model_name", "gpt-4")
    repos.setting_set(conn, "triage_api_key", "sk-key")
    repos.setting_set(conn, "triage_headers", "{}")
    # Seed issues via polling.
    class _FakeAdapter:
        def list_open_issues(self, owner: str, repo: str) -> list[IssueDTO]:
            return issues

        def list_open_pull_requests(self, owner: str, repo: str) -> list[Any]:
            return []

        def get_pull_request(self, owner: str, repo: str, number: int) -> Any:
            raise NotImplementedError

        def get_issue(self, owner: str, repo: str, number: int) -> Any:
            raise NotImplementedError

        def update_pr(self, owner: str, repo: str, number: int, body: str) -> None:
            raise NotImplementedError

    poll_project(conn, project, _FakeAdapter())
    conn.commit()
    return project


class _FakeTriageClient:
    def __init__(self, ranking: list[int]) -> None:
        self._ranking = ranking

    def call(self, config: Any, prompt: str) -> str:
        import json

        return json.dumps({"ranked_issue_numbers": self._ranking})


class _FakeLauncher:
    """Records launch/terminate calls. Creates agent_instances to simulate the real flow."""

    def __init__(self) -> None:
        self.launches: list[tuple[int, int]] = []  # (project_id, issue_number)
        self.terminations: list[int] = []  # agent_instance_ids

    def launch(self, conn: sqlite3.Connection, project_id: int, issue_number: int) -> int:
        instance = repos.agent_instance_create(
            conn,
            project_id=project_id,
            agent_type="implementor",
            issue_number=issue_number,
        )
        self.launches.append((project_id, issue_number))
        return instance.id

    def terminate(self, conn: sqlite3.Connection, agent_instance_id: int) -> None:
        repos.agent_instance_update(conn, agent_instance_id, status="terminated")
        self.terminations.append(agent_instance_id)


# ---------------------------------------------------------------------------
# Start / state tests
# ---------------------------------------------------------------------------


def test_start_loop_from_idle(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn, [])
    start_loop(conn, project.id)
    assert get_status(conn, project.id)["state"] == STATE_RUNNING


def test_start_loop_already_running_raises(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn, [])
    start_loop(conn, project.id)
    with pytest.raises(ImplementorLoopError, match="already running"):
        start_loop(conn, project.id)


def test_get_status_default_idle(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn, [])
    status = get_status(conn, project.id)
    assert status["state"] == STATE_IDLE
    assert status["running_implementors"] == 0


# ---------------------------------------------------------------------------
# Refill tests
# ---------------------------------------------------------------------------


def test_refill_launches_top_ranked_issue(conn: sqlite3.Connection) -> None:
    project = _seed_project(
        conn,
        [IssueDTO(1, "First", "open"), IssueDTO(3, "Third", "open")],
        parallelism=1,
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[3, 1])
    launcher = _FakeLauncher()

    result = refill(conn, project.id, triage, launcher)

    assert result.launched == 1
    assert launcher.launches == [(project.id, 3)]  # top-ranked
    issue = repos.github_issue_get(conn, project.id, 3)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_IN_PROGRESS


def test_refill_maintains_parallelism_n(conn: sqlite3.Connection) -> None:
    project = _seed_project(
        conn,
        [IssueDTO(1, "A", "open"), IssueDTO(2, "B", "open"), IssueDTO(3, "C", "open")],
        parallelism=2,
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[1, 2, 3])
    launcher = _FakeLauncher()

    result = refill(conn, project.id, triage, launcher)

    assert result.launched == 2  # N=2
    assert len(launcher.launches) == 2


def test_refill_no_duplicate_issue_assignment(conn: sqlite3.Connection) -> None:
    """FR-24: same issue cannot be assigned to two implementors."""
    project = _seed_project(
        conn,
        [IssueDTO(1, "A", "open"), IssueDTO(2, "B", "open")],
        parallelism=2,
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[1, 2])
    launcher = _FakeLauncher()

    # First refill: launches both issues (N=2).
    refill(conn, project.id, triage, launcher)
    assert len(launcher.launches) == 2

    # Second refill: both issues already assigned, no new launches.
    result = refill(conn, project.id, triage, launcher)
    assert result.launched == 0


def test_refill_skips_already_in_progress(conn: sqlite3.Connection) -> None:
    project = _seed_project(
        conn,
        [IssueDTO(1, "A", "open"), IssueDTO(2, "B", "open")],
        parallelism=1,
    )
    start_loop(conn, project.id)
    # Manually mark issue 1 as in-progress (simulating a prior launch).
    repos.github_issue_set_status(conn, project.id, 1, repos.ISSUE_STATUS_IN_PROGRESS)
    conn.commit()

    triage = _FakeTriageClient(ranking=[1, 2])
    launcher = _FakeLauncher()

    result = refill(conn, project.id, triage, launcher)

    # Issue 1 is in-progress, so issue 2 is launched instead.
    assert result.launched == 1
    assert launcher.launches == [(project.id, 2)]


def test_refill_drained_when_no_open_issues(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn, [], parallelism=1)
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[])
    launcher = _FakeLauncher()

    result = refill(conn, project.id, triage, launcher)

    assert result.launched == 0
    assert result.drained is True
    # Notification created (FR-29).
    notifications = repos.notification_list(conn, project_id=project.id)
    assert any("drained" in n["message"].lower() for n in notifications)
    # Loop set back to idle.
    assert get_status(conn, project.id)["state"] == STATE_IDLE


def test_refill_not_drained_when_implementors_running(conn: sqlite3.Connection) -> None:
    project = _seed_project(
        conn,
        [IssueDTO(1, "A", "open"), IssueDTO(2, "B", "open")],
        parallelism=1,
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[1, 2])
    launcher = _FakeLauncher()

    # First refill launches 1 implementor.
    refill(conn, project.id, triage, launcher)
    # Issue 2 still open but no slot → not drained.
    result = refill(conn, project.id, triage, launcher)
    assert result.drained is False


# ---------------------------------------------------------------------------
# Soft stop / hard stop tests
# ---------------------------------------------------------------------------


def test_soft_stop_no_new_launches(conn: sqlite3.Connection) -> None:
    """FR-30a: soft stop prevents new implementor launches."""
    project = _seed_project(
        conn,
        [IssueDTO(1, "A", "open"), IssueDTO(2, "B", "open")],
        parallelism=1,
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[1, 2])
    launcher = _FakeLauncher()

    # Launch 1 implementor first (N=1).
    refill(conn, project.id, triage, launcher)
    assert len(launcher.launches) == 1

    # Soft stop.
    soft_stop(conn, project.id)
    assert get_status(conn, project.id)["state"] == STATE_DRAINING

    # Refill in draining state → no new launches.
    result = refill(conn, project.id, triage, launcher)
    assert result.launched == 0


def test_hard_stop_terminates_all(conn: sqlite3.Connection) -> None:
    """FR-30b: hard stop terminates all running implementors."""
    project = _seed_project(
        conn,
        [IssueDTO(1, "A", "open"), IssueDTO(2, "B", "open")],
        parallelism=2,
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[1, 2])
    launcher = _FakeLauncher()

    refill(conn, project.id, triage, launcher)
    assert len(launcher.launches) == 2

    hard_stop(conn, project.id, launcher)

    assert len(launcher.terminations) == 2
    assert get_status(conn, project.id)["state"] == STATE_IDLE
    assert get_status(conn, project.id)["running_implementors"] == 0


# ---------------------------------------------------------------------------
# Draining → Idle transition tests (#33)
# ---------------------------------------------------------------------------


def test_check_draining_complete_transitions_to_idle(conn: sqlite3.Connection) -> None:
    """Draining with zero running implementors → Idle."""
    project = _seed_project(conn, [], parallelism=1)
    soft_stop(conn, project.id)
    assert get_status(conn, project.id)["state"] == STATE_DRAINING

    completed = check_draining_complete(conn, project.id)

    assert completed is True
    assert get_status(conn, project.id)["state"] == STATE_IDLE


def test_check_draining_complete_stays_draining_when_running(
    conn: sqlite3.Connection,
) -> None:
    """Draining with implementors still running stays Draining."""
    project = _seed_project(
        conn, [IssueDTO(1, "A", "open"), IssueDTO(2, "B", "open")], parallelism=1
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[1, 2])
    launcher = _FakeLauncher()
    refill(conn, project.id, triage, launcher)  # launches 1 implementor
    soft_stop(conn, project.id)

    completed = check_draining_complete(conn, project.id)

    assert completed is False
    assert get_status(conn, project.id)["state"] == STATE_DRAINING


def test_check_draining_complete_noop_in_idle(conn: sqlite3.Connection) -> None:
    """No effect on Idle state."""
    project = _seed_project(conn, [], parallelism=1)
    completed = check_draining_complete(conn, project.id)
    assert completed is False
    assert get_status(conn, project.id)["state"] == STATE_IDLE


def test_check_draining_complete_noop_in_running(conn: sqlite3.Connection) -> None:
    """No effect on Running state."""
    project = _seed_project(conn, [], parallelism=1)
    start_loop(conn, project.id)
    completed = check_draining_complete(conn, project.id)
    assert completed is False
    assert get_status(conn, project.id)["state"] == STATE_RUNNING


def test_refill_in_draining_transitions_to_idle_at_zero(
    conn: sqlite3.Connection,
) -> None:
    """refill in Draining state with zero running → Idle + drained notification."""
    project = _seed_project(
        conn, [IssueDTO(1, "A", "open")], parallelism=1
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[1])
    launcher = _FakeLauncher()

    # Launch 1 implementor, then soft stop.
    refill(conn, project.id, triage, launcher)
    soft_stop(conn, project.id)

    # Simulate implementor completion.
    for inst in repos.agent_instance_list_for_project(conn, project.id):
        if inst.agent_type == "implementor" and inst.status == "running":
            repos.agent_instance_update(conn, inst.id, status="terminated")
    conn.commit()

    result = refill(conn, project.id, triage, launcher)

    assert result.launched == 0
    assert result.drained is True
    assert get_status(conn, project.id)["state"] == STATE_IDLE
    notifications = repos.notification_list(conn, project_id=project.id)
    assert any("draining complete" in n["message"].lower() for n in notifications)


def test_refill_in_draining_stays_draining_when_running(
    conn: sqlite3.Connection,
) -> None:
    """refill in Draining state with implementors still running → stays Draining."""
    project = _seed_project(
        conn, [IssueDTO(1, "A", "open")], parallelism=1
    )
    start_loop(conn, project.id)
    triage = _FakeTriageClient(ranking=[1])
    launcher = _FakeLauncher()
    refill(conn, project.id, triage, launcher)
    soft_stop(conn, project.id)

    result = refill(conn, project.id, triage, launcher)

    assert result.launched == 0
    assert result.drained is False
    assert get_status(conn, project.id)["state"] == STATE_DRAINING


# ---------------------------------------------------------------------------
# Requeue tests
# ---------------------------------------------------------------------------


def test_requeue_failed_issue(conn: sqlite3.Connection) -> None:
    """FR-31: permanently-failed implementor's issue re-enters the eligible pool."""
    project = _seed_project(conn, [IssueDTO(1, "A", "open")], parallelism=1)
    # Mark issue as failed.
    repos.github_issue_set_status(conn, project.id, 1, repos.ISSUE_STATUS_FAILED)
    conn.commit()

    requeue_failed_issue(conn, project.id, 1)

    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_UNASSIGNED


def test_requeue_makes_issue_eligible_again(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn, [IssueDTO(1, "A", "open")], parallelism=1)
    start_loop(conn, project.id)
    repos.github_issue_set_status(conn, project.id, 1, repos.ISSUE_STATUS_FAILED)
    conn.commit()

    requeue_failed_issue(conn, project.id, 1)

    triage = _FakeTriageClient(ranking=[1])
    launcher = _FakeLauncher()
    result = refill(conn, project.id, triage, launcher)
    assert result.launched == 1  # re-queued issue is now eligible
