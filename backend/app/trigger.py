"""opencode trigger execution service (T07).

Runs first and subsequent opencode triggers inside an agent container via the
Docker adapter's streaming ``exec``. The first trigger captures the opencode
session id and persists it on the agent instance; subsequent triggers resume
the same session with ``--session <id>`` (FR-18). Scheduling enforces the
per-agent minimum gap (FR-17/FR-26) and prevents overlapping triggers (D26).

Non-zero ``opencode run`` exits are recorded as failed triggers, NOT container
failures — the restart counter is not touched (EC-3). A lost stream (no exit
code) is recorded as an incomplete trigger (EC-1/EC-2).

Console transport to the browser (T14) and backend-restart schedule resumption
(T13) are out of scope; captured output is persisted on the trigger record.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app import repositories as repos
from app.docker import DockerAdapter

if TYPE_CHECKING:
    from app.console import ConsoleBroker

# Number of seconds per minute, factored out for readable gap math.
_MINUTE_SECONDS = 60


class TriggerError(Exception):
    """Raised when a trigger cannot be scheduled or executed."""


# opencode prints the session id somewhere in its run output. This is the single
# integration point to adjust once the real opencode output format is confirmed;
# it accepts the JSON event form ("sessionID"/"session_id") and a plaintext
# fallback ("session <id>"). ponytail: one documented contract, not a parser lib.
_SESSION_ID_JSON_RE = re.compile(r'"session_?id"\s*:\s*"([^"]+)"', re.IGNORECASE)
_SESSION_ID_TEXT_RE = re.compile(r"\bsession\b[\s:]+([A-Za-z0-9_-]{8,})", re.IGNORECASE)


def extract_session_id(text: str) -> str | None:
    """Extract an opencode session id from one or more output lines."""
    for line in text.splitlines():
        match = _SESSION_ID_JSON_RE.search(line)
        if match:
            return match.group(1)
    match = _SESSION_ID_TEXT_RE.search(text)
    return match.group(1) if match else None


def build_trigger_command(
    model: str, agent: str, prompt: str, session_id: str | None
) -> list[str]:
    """Build the `opencode run` argv for a trigger (FR-16f, FR-18)."""
    cmd = ["opencode", "run", "-m", model, "--agent", agent]
    if session_id:
        cmd += ["--session", session_id]
    cmd.append(prompt)
    return cmd


@dataclass(frozen=True)
class TriggerRequest:
    """Everything needed to run one trigger for one agent."""

    agent_instance_id: int
    project_id: int
    container_id: str
    model: str  # "provider/model"
    agent_name: str
    prompt: str
    interval_minutes: int


@dataclass(frozen=True)
class TriggerOutcome:
    trigger_id: int
    exit_code: int | None
    incomplete: bool
    captured_session_id: str | None  # set only when a session id was captured this run


class TriggerScheduler:
    """Per-agent minimum-gap + non-overlap scheduling.

    In-memory state keyed by agent instance id. Durable schedule resumption
    after a backend restart is T13 (out of scope here). ``clock`` is injectable
    for tests (epoch seconds, default :func:`time.time`).
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._last_start: dict[int, float] = {}
        self._in_progress: set[int] = set()

    def last_started_at(self, agent_instance_id: int) -> float | None:
        return self._last_start.get(agent_instance_id)

    def is_due(self, agent_instance_id: int, interval_minutes: int) -> bool:
        """True when a trigger may fire: no overlap and the gap has elapsed."""
        if agent_instance_id in self._in_progress:
            return False
        last = self._last_start.get(agent_instance_id)
        if last is None:
            return True
        return (self._clock() - last) >= interval_minutes * _MINUTE_SECONDS

    def try_acquire(
        self, agent_instance_id: int, interval_minutes: int, *, manual: bool = False
    ) -> bool:
        """Atomically check + mark a trigger start. Returns False if blocked.

        Blocked by an in-flight trigger (overlap) or, when not manual, by the
        minimum gap not having elapsed since the previous trigger's start.
        """
        if agent_instance_id in self._in_progress:
            return False
        if not manual and not self.is_due(agent_instance_id, interval_minutes):
            return False
        self._in_progress.add(agent_instance_id)
        self._last_start[agent_instance_id] = self._clock()
        return True

    def release(self, agent_instance_id: int) -> None:
        self._in_progress.discard(agent_instance_id)

    def seed(self, agent_instance_id: int) -> None:
        """Resume scheduling for an agent after backend restart.

        Next fire is treated as restart time + interval (T13).
        """
        self._last_start[agent_instance_id] = self._clock()

    @property
    def in_progress(self) -> frozenset[int]:
        return frozenset(self._in_progress)


class TriggerService:
    """Runs opencode triggers, captures sessions, and persists lifecycle records."""

    def __init__(
        self,
        adapter: DockerAdapter,
        scheduler: TriggerScheduler | None = None,
        console_broker: ConsoleBroker | None = None,
    ) -> None:
        self._adapter = adapter
        self._scheduler = scheduler or TriggerScheduler()
        self._console_broker = console_broker

    @property
    def scheduler(self) -> TriggerScheduler:
        return self._scheduler

    def seed_running(self, conn: sqlite3.Connection) -> None:
        """Seed scheduler last-start times for all running agent instances."""
        for inst in repos.agent_instance_list_running(conn):
            self._scheduler.seed(inst.id)

    def is_due(self, req: TriggerRequest) -> bool:
        return self._scheduler.is_due(req.agent_instance_id, req.interval_minutes)

    def run(
        self,
        conn: sqlite3.Connection,
        req: TriggerRequest,
        *,
        manual: bool = False,
    ) -> TriggerOutcome:
        """Execute one trigger. Raises :class:`TriggerError` if not due/overlap."""
        if not self._scheduler.try_acquire(
            req.agent_instance_id, req.interval_minutes, manual=manual
        ):
            if req.agent_instance_id in self._scheduler.in_progress:
                raise TriggerError(
                    f"trigger already in progress for agent {req.agent_instance_id}"
                )
            raise TriggerError(
                f"minimum gap not elapsed for agent {req.agent_instance_id}; "
                "use manual=True to force"
            )
        try:
            return self._run(conn, req, manual=manual)
        finally:
            self._scheduler.release(req.agent_instance_id)

    def _run(
        self, conn: sqlite3.Connection, req: TriggerRequest, *, manual: bool
    ) -> TriggerOutcome:
        instance = repos.agent_instance_get(conn, req.agent_instance_id)
        if instance is None:
            raise TriggerError(f"agent instance {req.agent_instance_id} not found")
        session_id = instance.session_id
        command = build_trigger_command(
            req.model, req.agent_name, req.prompt, session_id
        )

        trigger = repos.trigger_create(conn, req.agent_instance_id)
        exit_code: int | None = None
        output = ""
        captured: str | None = None
        # FR-33: Persist each output line to console_chunks for replay/streaming (T14).
        chunk_idx = repos.console_chunk_next_index(conn, req.agent_instance_id)
        try:
            stream = self._adapter.exec_stream(req.container_id, command)
            chunks: list[str] = []
            for line in stream:
                chunks.append(line)
                if session_id is None and captured is None:
                    captured = extract_session_id(line)
                # Write to durable store + publish to live subscribers.
                repos.console_chunk_append(conn, req.agent_instance_id, chunk_idx, line)
                if self._console_broker:
                    self._console_broker.publish(req.agent_instance_id, line)
                chunk_idx += 1
            output = "".join(chunks)
            exit_code = stream.exit_code
        finally:
            # Record the outcome even if streaming raised (EC-1/EC-2 incomplete).
            repos.trigger_finish(conn, trigger.id, exit_code, output)

        captured_session: str | None = None
        if session_id is None and captured:
            # First trigger: persist the captured session id (FR-16g).
            repos.agent_instance_update(conn, req.agent_instance_id, session_id=captured)
            captured_session = captured

        incomplete = exit_code is None
        repos.audit_event_create(
            conn,
            "trigger",
            project_id=req.project_id,
            agent_instance_id=req.agent_instance_id,
            payload={
                "trigger_id": trigger.id,
                "exit_code": exit_code,
                "incomplete": incomplete,
                "manual": manual,
            },
        )
        return TriggerOutcome(
            trigger_id=trigger.id,
            exit_code=exit_code,
            incomplete=incomplete,
            captured_session_id=captured_session,
        )


def trigger_request_for_instance(
    conn: sqlite3.Connection,
    agent_instance_id: int,
    prompt: str,
    interval_minutes: int,
) -> TriggerRequest | None:
    """Build a TriggerRequest from an agent instance + its launch config snapshot.

    Returns None if the instance or its snapshot is missing the required fields.
    """
    instance = repos.agent_instance_get(conn, agent_instance_id)
    if instance is None or not instance.container_id:
        return None
    snapshot = repos.config_snapshot_get(conn, agent_instance_id)
    if not snapshot:
        return None
    provider = snapshot.get("model_provider_id")
    model_id = snapshot.get("model_id")
    agent_name = snapshot.get("agent_name")
    if not (provider and model_id and agent_name):
        return None
    return TriggerRequest(
        agent_instance_id=instance.id,
        project_id=instance.project_id,
        container_id=instance.container_id,
        model=f"{provider}/{model_id}",
        agent_name=agent_name,
        prompt=prompt,
        interval_minutes=interval_minutes,
    )
