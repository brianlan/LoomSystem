"""Destructive cleanup: project deletion cascade and implementor hard-kill (T12).

Thin domain layer that iterates agent instances, stops/removes containers,
cleans up credential dirs, and persists audit events. Idempotent: tolerates
containers that are already missing (D27, AC-10).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import repositories as repos
from app.docker import DockerAdapter
from app.launch import cleanup_credential_dir


class CleanupError(Exception):
    """Raised when a cleanup operation fails."""


@dataclass(frozen=True)
class HardKillResult:
    killed_instance_ids: list[int]


def _terminate_instance(
    conn: sqlite3.Connection, instance: repos.AgentInstance, adapter: DockerAdapter
) -> None:
    """Stop+remove container, cleanup creds, mark terminated."""
    if instance.container_id:
        adapter.stop(instance.container_id)
        adapter.remove(instance.container_id)

    snapshot = repos.config_snapshot_get(conn, instance.id)
    if snapshot and snapshot.get("credential_dir"):
        cleanup_credential_dir(Path(snapshot["credential_dir"]))

    repos.agent_instance_update(conn, instance.id, status="terminated")


def delete_project_cascade(
    conn: sqlite3.Connection, project_id: int, adapter: DockerAdapter
) -> None:
    """Delete a project and cascade-kill all its agents (FR-6, FR-30, D23).

    Stops and removes every reviewer/implementor container owned by the
    project, cleans up credential dirs, marks instances terminated, then
    deletes the project record. Tolerates already-missing containers.
    """
    project = repos.project_get(conn, project_id)
    if project is None:
        raise CleanupError(f"Project {project_id} not found")

    instances = repos.agent_instance_list_for_project(conn, project_id)
    for inst in instances:
        _terminate_instance(conn, inst, adapter)

    repos.audit_event_create(
        conn,
        "project_deleted",
        project_id=project_id,
        payload={"terminated_instances": [inst.id for inst in instances]},
    )
    repos.project_delete(conn, project_id)


def hard_kill_implementors(
    conn: sqlite3.Connection, project_id: int, adapter: DockerAdapter
) -> HardKillResult:
    """Hard-kill all running implementors for a project (FR-30, D27, AC-10).

    Terminates every running implementor immediately. Does NOT delete the
    project record. Tolerates already-missing containers.
    """
    project = repos.project_get(conn, project_id)
    if project is None:
        raise CleanupError(f"Project {project_id} not found")

    instances = repos.agent_instance_list_for_project(conn, project_id)
    killed: list[int] = []
    for inst in instances:
        if inst.agent_type == "implementor" and inst.status == "running":
            _terminate_instance(conn, inst, adapter)
            killed.append(inst.id)

    repos.audit_event_create(
        conn,
        "implementor_hard_kill",
        project_id=project_id,
        payload={"killed_instances": killed},
    )
    return HardKillResult(killed_instance_ids=killed)
