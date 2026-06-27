"""Implementor loop orchestration: state machine, triage-driven refill, stop/drain.

Keeps this issue focused on domain rules and state transitions (per the issue's
chosen approach). Uses Protocol interfaces for launch/terminate so tests fake
runtime behavior without Docker. T11 wires the real lifecycle.

State machine: Idle → Running → (Draining|Idle). Refill runs triage, picks
top-N eligible issues, launches up to N implementors (1:1 issue binding, FR-24).
Soft stop = Draining (no new launches). Hard stop = terminate all, back to Idle.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from app import repositories as repos
from app.triage import TriageClient, rank_issues

# Loop states.
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_DRAINING = "draining"

SETTING_KEY = "implementor_loop_state"


class ImplementorLoopError(Exception):
    pass


class ImplementorLauncher(Protocol):
    """Service interface for launching/terminating implementor agents.

    T11 wires this to the real Docker launch service. Tests inject a fake.
    """

    def launch(self, conn: sqlite3.Connection, project_id: int, issue_number: int) -> int:
        """Launch an implementor for the given issue. Returns the agent_instance_id."""
        ...

    def terminate(self, conn: sqlite3.Connection, agent_instance_id: int) -> None:
        """Stop and remove the implementor container + mark instance terminated."""
        ...


def _get_state(conn: sqlite3.Connection, project_id: int) -> str:
    raw = repos.setting_get(conn, f"{SETTING_KEY}:{project_id}")
    return raw if raw in (STATE_IDLE, STATE_RUNNING, STATE_DRAINING) else STATE_IDLE


def _set_state(conn: sqlite3.Connection, project_id: int, state: str) -> None:
    repos.setting_set(conn, f"{SETTING_KEY}:{project_id}", state)
    # OBS-1: record every state transition.
    repos.audit_event_create(
        conn,
        "state_transition",
        project_id=project_id,
        payload={"to": state},
    )


def _eligible_issues(
    conn: sqlite3.Connection, project_id: int
) -> list[int]:
    """Open issues in the 'unassigned' pool — candidates for assignment."""
    issues = repos.github_issue_list(conn, project_id)
    return [
        i.issue_number
        for i in issues
        if i.state == "open" and i.loom_status == repos.ISSUE_STATUS_UNASSIGNED
    ]


def _assigned_issues(conn: sqlite3.Connection, project_id: int) -> set[int]:
    """Issue numbers already bound to a running implementor."""
    instances = repos.agent_instance_list_for_project(conn, project_id)
    return {
        inst.issue_number
        for inst in instances
        if inst.agent_type == "implementor"
        and inst.status == "running"
        and inst.issue_number is not None
    }


def _running_implementor_count(conn: sqlite3.Connection, project_id: int) -> int:
    instances = repos.agent_instance_list_for_project(conn, project_id)
    return sum(
        1
        for inst in instances
        if inst.agent_type == "implementor" and inst.status == "running"
    )


def _parallelism(project: repos.Project) -> int:
    return int(project.implementor_config.get("parallelism", 1))


@dataclass(frozen=True)
class RefillResult:
    launched: int
    drained: bool


def start_loop(conn: sqlite3.Connection, project_id: int) -> None:
    """FR-21: Start the implementor loop for a project."""
    state = _get_state(conn, project_id)
    if state == STATE_RUNNING:
        raise ImplementorLoopError("Implementor loop is already running")
    _set_state(conn, project_id, STATE_RUNNING)


def refill(
    conn: sqlite3.Connection,
    project_id: int,
    triage_client: TriageClient,
    launcher: ImplementorLauncher,
) -> RefillResult:
    """FR-22/FR-23: Run triage, then launch implementors up to configured parallelism.

    In Draining state, checks whether all implementors have finished and
    transitions back to Idle if so. Returns launched count and drained flag.
    """
    state = _get_state(conn, project_id)
    if state == STATE_DRAINING:
        if check_draining_complete(conn, project_id):
            return RefillResult(launched=0, drained=True)
        return RefillResult(launched=0, drained=False)
    if state != STATE_RUNNING:
        return RefillResult(launched=0, drained=False)

    project = repos.project_get(conn, project_id)
    if project is None:
        raise ImplementorLoopError(f"Project {project_id} not found")

    # Run triage to get the ranked issue list (FR-22).
    ranked = rank_issues(conn, project_id, triage_client)

    # Determine how many slots are available (FR-23: maintain N concurrent).
    n = _parallelism(project)
    running = _running_implementor_count(conn, project_id)
    slots = max(0, n - running)
    if slots == 0:
        return RefillResult(launched=0, drained=False)

    # Pick top-ranked eligible issues not already assigned or in-progress (FR-24: 1:1 binding).
    assigned = _assigned_issues(conn, project_id)
    in_progress = {
        i.issue_number
        for i in repos.github_issue_list(conn, project_id)
        if i.loom_status == repos.ISSUE_STATUS_IN_PROGRESS
    }
    taken = assigned | in_progress
    eligible = [num for num in ranked if num not in taken]
    to_launch = eligible[:slots]

    for issue_number in to_launch:
        launcher.launch(conn, project_id, issue_number)
        # Mark the issue as in-progress so it's not re-assigned.
        repos.github_issue_set_status(
            conn, project_id, issue_number, repos.ISSUE_STATUS_IN_PROGRESS
        )

    launched = len(to_launch)

    # FR-29: Backlog drained — no open eligible issues AND no running implementors.
    open_eligible = _eligible_issues(conn, project_id)
    still_running = _running_implementor_count(conn, project_id)
    drained = len(open_eligible) == 0 and still_running == 0
    if drained:
        repos.notification_create(
            conn,
            message=f"Backlog drained for project '{project.name}'",
            project_id=project_id,
        )
        _set_state(conn, project_id, STATE_IDLE)

    return RefillResult(launched=launched, drained=drained)


def soft_stop(conn: sqlite3.Connection, project_id: int) -> None:
    """FR-30a: Soft stop — no new implementor launches; running implementors continue."""
    _set_state(conn, project_id, STATE_DRAINING)


def check_draining_complete(conn: sqlite3.Connection, project_id: int) -> bool:
    """FR-30a: Transition Draining → Idle once all running implementors finish.

    Returns True if the transition was made. No-op in other states.
    Also callable from T11's heartbeat poll (#11) without modification.
    """
    if _get_state(conn, project_id) != STATE_DRAINING:
        return False
    if _running_implementor_count(conn, project_id) > 0:
        return False
    _set_state(conn, project_id, STATE_IDLE)
    project = repos.project_get(conn, project_id)
    if project is not None:
        repos.notification_create(
            conn,
            message=f"Draining complete for project '{project.name}'",
            project_id=project_id,
        )
    return True


def hard_stop(
    conn: sqlite3.Connection,
    project_id: int,
    launcher: ImplementorLauncher,
) -> None:
    """FR-30b: Hard stop — terminate all running implementors immediately."""
    instances = repos.agent_instance_list_for_project(conn, project_id)
    for inst in instances:
        if inst.agent_type == "implementor" and inst.status == "running":
            launcher.terminate(conn, inst.id)
    _set_state(conn, project_id, STATE_IDLE)


def requeue_failed_issue(
    conn: sqlite3.Connection,
    project_id: int,
    issue_number: int,
) -> None:
    """FR-31: Re-queue a permanently-failed implementor's issue into the eligible pool."""
    repos.github_issue_set_status(
        conn, project_id, issue_number, repos.ISSUE_STATUS_UNASSIGNED
    )


def get_status(conn: sqlite3.Connection, project_id: int) -> dict[str, object]:
    """Expose the loop state and running implementor count for project views."""
    state = _get_state(conn, project_id)
    running = _running_implementor_count(conn, project_id)
    return {"state": state, "running_implementors": running}
