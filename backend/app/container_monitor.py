"""Container monitoring, auto-restart with cap, and backend restart recovery (T13).

A background loop inspects containers for all running agent instances. Dead
containers are restarted up to a configurable cap within a sliding window;
permanent failures mark the agent failed, notify the operator, and re-queue any
bound implementor issue. On backend startup surviving containers are reconnected
and their schedules are resumed from the restart time.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from app import implementor_loop as impl_loop
from app import repositories as repos
from app.docker import ContainerInfo, DockerAdapter, DockerError
from app.launch import (
    LaunchError,
    LaunchSpec,
    cleanup_credential_dir,
    create_agent_container,
)
from app.trigger import TriggerService

logger = logging.getLogger(__name__)

DEFAULT_RETRY_CAP = 5
RETRY_WINDOW_SECONDS = 3600
MONITOR_INTERVAL_SECONDS = 10


class RecoveryError(Exception):
    """Raised when restart recovery cannot proceed for an instance."""


def _build_spec_for_instance(
    conn: sqlite3.Connection, instance: repos.AgentInstance
) -> LaunchSpec:
    """Reconstruct a LaunchSpec from the persisted instance + snapshot + registry."""
    snapshot = repos.config_snapshot_get(conn, instance.id)
    if not snapshot:
        raise RecoveryError(f"No config snapshot for instance {instance.id}")

    project = repos.project_get(conn, instance.project_id)
    if project is None:
        raise RecoveryError(f"Project {instance.project_id} not found")

    agent_def_id = instance.agent_definition_id or snapshot.get("agent_definition_id")
    model_entry_id = instance.model_entry_id or snapshot.get("model_entry_id")
    docker_image_id = instance.docker_image_id or snapshot.get("docker_image_id")

    agent_def = repos.agent_definition_get(conn, agent_def_id) if agent_def_id else None
    model = repos.model_entry_get(conn, model_entry_id) if model_entry_id else None
    image = repos.docker_image_get(conn, docker_image_id) if docker_image_id else None

    if agent_def is None:
        raise RecoveryError(f"Agent definition missing for instance {instance.id}")
    if model is None:
        raise RecoveryError(f"Model entry missing for instance {instance.id}")
    if image is None:
        raise RecoveryError(f"Docker image missing for instance {instance.id}")

    ssh_key = repos.setting_get(conn, "ssh_key")
    if not ssh_key:
        raise RecoveryError("SSH key not configured")

    proxy = snapshot.get("proxy") or {}
    return LaunchSpec(
        project_id=project.id,
        project_name=project.name,
        repo_url=snapshot.get("repo_url", project.repo_url),
        agent_type=instance.agent_type,
        agent_definition_id=agent_def.id,
        agent_name=snapshot.get("agent_name", agent_def.name),
        prompt_markdown=agent_def.prompt_markdown,
        github_identity=snapshot.get("github_identity", agent_def.github_identity),
        model_entry_id=model.id,
        model_provider_id=model.provider_id,
        model_id=snapshot.get("model_id", model.model_id),
        model_credentials=model.credentials,
        model_custom_config=model.custom_config,
        docker_image_id=image.id,
        docker_image_name=snapshot.get("docker_image_name", image.image_name),
        ssh_key=ssh_key,
        http_proxy=proxy.get("http") or repos.setting_get(conn, "http_proxy") or None,
        https_proxy=proxy.get("https") or repos.setting_get(conn, "https_proxy") or None,
    )


def _next_retry_count(
    last_restart_at: str | None, current_count: int, now: float
) -> int:
    """Return the retry count to record for a restart happening now.

    If the previous restart was outside the retry window, start a new burst at 1.
    Otherwise the burst continues from the existing count.
    """
    if last_restart_at is None:
        return 1
    # SQLite timestamps are ISO-8601 strings; parse naively as UTC-ish.
    try:
        prev = _parse_sqlite_timestamp(last_restart_at)
    except ValueError:
        return 1
    if (now - prev) > RETRY_WINDOW_SECONDS:
        return 1
    return current_count + 1


def _parse_sqlite_timestamp(value: str) -> float:
    """Parse an ISO-8601-ish SQLite timestamp to epoch seconds."""
    from datetime import datetime, timezone

    # SQLite CURRENT_TIMESTAMP yields "YYYY-MM-DD HH:MM:SS" (UTC).
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp {value}")


def _format_sqlite_timestamp(now: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _mark_failed(
    conn: sqlite3.Connection,
    instance: repos.AgentInstance,
    reason: str,
) -> None:
    """Mark instance failed, notify, audit, and re-queue any implementor issue."""
    repos.agent_instance_update(conn, instance.id, status="failed")
    repos.notification_create(
        conn,
        message=f"Agent {instance.agent_type} {instance.id} permanently failed: {reason}",
        project_id=instance.project_id,
        agent_instance_id=instance.id,
    )
    repos.audit_event_create(
        conn,
        "agent_failed",
        project_id=instance.project_id,
        agent_instance_id=instance.id,
        payload={"reason": reason, "restart_count": instance.restart_count},
    )
    if instance.agent_type == "implementor" and instance.issue_number is not None:
        impl_loop.requeue_failed_issue(conn, instance.project_id, instance.issue_number)


def relaunch_agent(
    conn: sqlite3.Connection,
    instance_id: int,
    adapter: DockerAdapter,
) -> bool:
    """Restart an existing agent instance with a fresh container.

    Returns True when a new container is running. On failure the instance is
    marked failed and any bound implementor issue is re-queued.
    """
    instance = repos.agent_instance_get(conn, instance_id)
    if instance is None:
        raise RecoveryError(f"Agent instance {instance_id} not found")

    spec = _build_spec_for_instance(conn, instance)
    # Use the deterministic naming helpers from launch.py via local imports to
    # avoid a circular import at module load time.
    from app.launch import _container_name, _labels

    name = _container_name(spec.project_id, spec.agent_type)
    labels = _labels(spec.project_id, spec.agent_type, instance.id)

    old_snapshot = repos.config_snapshot_get(conn, instance.id) or {}
    old_credential_dir: Path | None = None
    if old_snapshot.get("credential_dir"):
        old_credential_dir = Path(old_snapshot["credential_dir"])

    try:
        result = create_agent_container(spec, adapter, name, labels)
    except LaunchError as exc:
        _mark_failed(conn, instance, f"restart failed: {exc}")
        return False

    if old_credential_dir is not None:
        cleanup_credential_dir(old_credential_dir)

    new_snapshot = {
        **old_snapshot,
        "container_name": result.container_name,
        "container_labels": result.labels,
        "credential_dir": str(result.credential_dir),
    }
    repos.config_snapshot_update(conn, instance.id, new_snapshot)
    repos.agent_instance_update(
        conn,
        instance.id,
        container_id=result.container_id,
        container_name=result.container_name,
    )
    repos.audit_event_create(
        conn,
        "agent_restarted",
        project_id=instance.project_id,
        agent_instance_id=instance.id,
        payload={
            "container_id": result.container_id,
            "container_name": result.container_name,
        },
    )
    return True


def _inspect_or_missing(adapter: DockerAdapter, container_id: str | None) -> ContainerInfo | None:
    """Inspect a container, returning None if it is missing or the id is empty."""
    if not container_id:
        return None
    try:
        return adapter.inspect(container_id)
    except DockerError:
        return None


def _handle_dead_container(
    conn: sqlite3.Connection,
    instance: repos.AgentInstance,
    adapter: DockerAdapter,
    retry_cap: int,
    now: float,
) -> None:
    """Decide whether to restart or permanently fail a dead container."""
    # OBS-3: record the container die event before recovery.
    repos.audit_event_create(
        conn,
        "container_die",
        project_id=instance.project_id,
        agent_instance_id=instance.id,
        payload={"container_id": instance.container_id, "restart_count": instance.restart_count},
    )
    new_count = _next_retry_count(instance.last_restart_at, instance.restart_count, now)

    if new_count > retry_cap:
        _mark_failed(conn, instance, f"retry cap ({retry_cap}) exceeded within window")
        return


    repos.agent_instance_update(
        conn,
        instance.id,
        restart_count=new_count,
        last_restart_at=_format_sqlite_timestamp(now),
    )
    # Refresh instance state for downstream logic.
    refreshed = repos.agent_instance_get(conn, instance.id)
    if refreshed is None:
        return
    relaunch_agent(conn, refreshed.id, adapter)


def check_containers(
    conn: sqlite3.Connection,
    adapter: DockerAdapter,
    *,
    retry_cap: int = DEFAULT_RETRY_CAP,
    now: float | None = None,
) -> None:
    """Inspect all running agent containers and recover any that are dead."""
    now = now if now is not None else time.time()
    for instance in repos.agent_instance_list_running(conn):
        info = _inspect_or_missing(adapter, instance.container_id)
        if info is not None and info.state == "running":
            continue
        _handle_dead_container(conn, instance, adapter, retry_cap, now)


# ---------------------------------------------------------------------------
# Background monitor service
# ---------------------------------------------------------------------------


class ContainerMonitor:
    """Periodic container health monitor."""

    def __init__(
        self,
        db_path: Path | str | None,
        adapter: DockerAdapter,
        *,
        retry_cap: int = DEFAULT_RETRY_CAP,
        interval_seconds: float = MONITOR_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        from app.db import get_db

        self._db = get_db(db_path)
        self._adapter = adapter
        self._retry_cap = retry_cap
        self._interval = interval_seconds
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            try:
                with self._db.connect() as conn:
                    check_containers(
                        conn, self._adapter, retry_cap=self._retry_cap, now=self._clock()
                    )
                    conn.commit()
            except Exception:  # pragma: no cover - defensive: never kill the loop
                logger.exception("Container monitor iteration failed")
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


# ---------------------------------------------------------------------------
# Backend startup recovery
# ---------------------------------------------------------------------------


def _record_abandoned_triggers(conn: sqlite3.Connection) -> None:
    """Mark any in-flight triggers as incomplete (backend restart, EC-1/EC-2)."""
    rows = conn.execute(
        "SELECT id, agent_instance_id FROM triggers WHERE ended_at IS NULL"
    ).fetchall()
    for row in rows:
        repos.trigger_finish(
            conn,
            row["id"],
            exit_code=None,
            output="abandoned by backend restart",
        )
        repos.audit_event_create(
            conn,
            "trigger_abandoned",
            agent_instance_id=row["agent_instance_id"],
            payload={"trigger_id": row["id"]},
        )


def _reconnect_surviving(
    conn: sqlite3.Connection,
    instance: repos.AgentInstance,
) -> None:
    """Audit that a container survived the backend restart."""
    repos.audit_event_create(
        conn,
        "agent_reconnected",
        project_id=instance.project_id,
        agent_instance_id=instance.id,
        payload={"container_id": instance.container_id},
    )


def recover_on_startup(
    conn: sqlite3.Connection,
    adapter: DockerAdapter,
    trigger_service: TriggerService | None = None,
    *,
    retry_cap: int = DEFAULT_RETRY_CAP,
    now: float | None = None,
) -> None:
    """Recover from a backend restart.

    - Records abandoned in-flight triggers as incomplete.
    - Reconnects to surviving containers.
    - Restarts missing/dead containers when under the retry cap.
    - Resumes trigger scheduling from restart time + interval.
    """
    now = now if now is not None else time.time()
    _record_abandoned_triggers(conn)
    for instance in repos.agent_instance_list_running(conn):
        info = _inspect_or_missing(adapter, instance.container_id)
        if info is not None and info.state == "running":
            _reconnect_surviving(conn, instance)
        else:
            _handle_dead_container(conn, instance, adapter, retry_cap, now)
    if trigger_service is not None:
        trigger_service.seed_running(conn)
