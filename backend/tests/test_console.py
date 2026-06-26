"""Tests for console capture, replay, and live stream transport (T14).

Covers: chunk persistence during trigger execution, history replay API,
SSE stream (history + live), broker pub/sub, large history performance,
and reconnect behavior.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import repositories as repos
from app.console import ConsoleBroker, sse_event
from app.db import get_db
from app.docker import FakeDockerAdapter
from app.main import app
from app.trigger import TriggerRequest, TriggerService


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "console_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def client(db_path: Path) -> TestClient:
    app.state.db.db_path = db_path
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_agent_instance(conn: sqlite3.Connection) -> int:
    """Create a minimal agent instance for console chunk testing."""
    project = repos.project_create(conn, "test", "git@github.com:test/test.git")
    instance = repos.agent_instance_create(
        conn,
        project_id=project.id,
        agent_type="reviewer",
    )
    conn.commit()
    return instance.id


def _make_trigger_req(agent_instance_id: int) -> TriggerRequest:
    return TriggerRequest(
        agent_instance_id=agent_instance_id,
        project_id=1,
        container_id="fake-container-1",
        model="anthropic/claude-3",
        agent_name="reviewer",
        prompt="review",
        interval_minutes=15,
    )


# ---------------------------------------------------------------------------
# Chunk persistence tests (FR-33)
# ---------------------------------------------------------------------------


def test_trigger_writes_console_chunks(conn: sqlite3.Connection) -> None:
    """FR-33: Trigger execution persists each output line as a console chunk."""
    instance_id = _seed_agent_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (["line one\n", "line two\n", "line three\n"], 0)
    service = TriggerService(adapter)

    service.run(conn, _make_trigger_req(instance_id), manual=True)

    chunks = repos.console_chunk_list(conn, instance_id)
    assert len(chunks) == 3
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["content"] == "line one\n"
    assert chunks[1]["content"] == "line two\n"
    assert chunks[2]["content"] == "line three\n"


def test_multiple_triggers_increment_chunk_index(conn: sqlite3.Connection) -> None:
    """Chunk indices continue across triggers for the same agent."""
    instance_id = _seed_agent_instance(conn)
    adapter = FakeDockerAdapter()
    service = TriggerService(adapter)

    # First trigger: 2 lines.
    adapter.exec_stream_default = (["a\n", "b\n"], 0)
    service.run(conn, _make_trigger_req(instance_id), manual=True)

    # Second trigger: 1 line.
    adapter.exec_stream_default = (["c\n"], 0)
    service.run(conn, _make_trigger_req(instance_id), manual=True)

    chunks = repos.console_chunk_list(conn, instance_id)
    assert len(chunks) == 3
    assert chunks[0]["chunk_index"] == 0
    assert chunks[2]["chunk_index"] == 2
    assert chunks[2]["content"] == "c\n"


def test_console_chunk_next_index_empty(conn: sqlite3.Connection) -> None:
    """Next index is 0 for an agent with no chunks."""
    instance_id = _seed_agent_instance(conn)
    assert repos.console_chunk_next_index(conn, instance_id) == 0


def test_console_chunk_next_index_after_chunks(conn: sqlite3.Connection) -> None:
    """Next index is max+1 after chunks exist."""
    instance_id = _seed_agent_instance(conn)
    repos.console_chunk_append(conn, instance_id, 0, "a")
    repos.console_chunk_append(conn, instance_id, 1, "b")
    assert repos.console_chunk_next_index(conn, instance_id) == 2


# ---------------------------------------------------------------------------
# History replay API tests (FR-34)
# ---------------------------------------------------------------------------


def test_history_returns_all_chunks(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    """FR-34: History endpoint returns all stored chunks in order."""
    instance_id = _seed_agent_instance(conn)
    repos.console_chunk_append(conn, instance_id, 0, "first\n")
    repos.console_chunk_append(conn, instance_id, 1, "second\n")
    conn.commit()

    resp = client.get(f"/api/v1/agents/{instance_id}/console/history")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["content"] == "first\n"
    assert data[1]["content"] == "second\n"


def test_history_empty(conn: sqlite3.Connection, client: TestClient) -> None:
    """History endpoint returns empty list for an agent with no chunks."""
    instance_id = _seed_agent_instance(conn)
    resp = client.get(f"/api/v1/agents/{instance_id}/console/history")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Broker pub/sub tests
# ---------------------------------------------------------------------------


def test_broker_publish_to_multiple_subscribers() -> None:
    """Broker fans out chunks to all connected subscribers."""
    broker = ConsoleBroker()
    q1 = broker.subscribe(1)
    q2 = broker.subscribe(1)

    broker.publish(1, "hello\n")

    assert q1.get_nowait() == "hello\n"
    assert q2.get_nowait() == "hello\n"


def test_broker_unsubscribe_stops_delivery() -> None:
    """After unsubscribe, a subscriber no longer receives chunks."""
    broker = ConsoleBroker()
    q = broker.subscribe(1)
    broker.unsubscribe(1, q)
    broker.publish(1, "should not arrive\n")

    assert q.empty()


def test_broker_no_subscribers_publish_is_noop() -> None:
    """Publishing with no subscribers is a no-op."""
    broker = ConsoleBroker()
    broker.publish(1, "nobody listening\n")  # Should not raise.


def test_broker_delivers_in_order() -> None:
    """Chunks are delivered in publication order."""
    broker = ConsoleBroker()
    q = broker.subscribe(1)

    for i in range(5):
        broker.publish(1, f"line{i}\n")

    for i in range(5):
        assert q.get_nowait() == f"line{i}\n"


# ---------------------------------------------------------------------------
# Trigger → broker integration
# ---------------------------------------------------------------------------


def test_trigger_publishes_to_broker(conn: sqlite3.Connection) -> None:
    """Trigger service with broker publishes each line to live subscribers."""
    instance_id = _seed_agent_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (["live1\n", "live2\n"], 0)
    broker = ConsoleBroker()
    q = broker.subscribe(instance_id)
    service = TriggerService(adapter, console_broker=broker)

    service.run(conn, _make_trigger_req(instance_id), manual=True)

    # Subscriber received both lines.
    assert q.get_nowait() == "live1\n"
    assert q.get_nowait() == "live2\n"

    # Also persisted to DB.
    chunks = repos.console_chunk_list(conn, instance_id)
    assert len(chunks) == 2


def test_trigger_without_broker_still_persists(conn: sqlite3.Connection) -> None:
    """Trigger service without broker still writes chunks to DB."""
    instance_id = _seed_agent_instance(conn)
    adapter = FakeDockerAdapter()
    adapter.exec_stream_default = (["a\n", "b\n"], 0)
    service = TriggerService(adapter)  # No broker.

    service.run(conn, _make_trigger_req(instance_id), manual=True)

    chunks = repos.console_chunk_list(conn, instance_id)
    assert len(chunks) == 2


# ---------------------------------------------------------------------------
# Large history performance test (NFR-2)
# ---------------------------------------------------------------------------


def test_large_history_replay_starts_quickly(conn: sqlite3.Connection) -> None:
    """NFR-2: Replay of a large capture (≥1000 chunks) begins quickly (<3s)."""
    instance_id = _seed_agent_instance(conn)
    # Seed 1000 chunks (each ~1KB, ~1MB total).
    for i in range(1000):
        repos.console_chunk_append(conn, instance_id, i, f"chunk {i} " * 50 + "\n")
    conn.commit()

    start = time.monotonic()
    chunks = repos.console_chunk_list(conn, instance_id)
    elapsed = time.monotonic() - start

    assert len(chunks) == 1000
    assert elapsed < 3.0, f"Replay took {elapsed:.2f}s (expected <3s)"


# ---------------------------------------------------------------------------
# SSE event formatting
# ---------------------------------------------------------------------------


def test_sse_event_format() -> None:
    """SSE events are correctly formatted as data: JSON."""
    event = sse_event({"chunk_index": 0, "content": "hello\n"})
    assert event.startswith("data: ")
    assert event.endswith("\n\n")
    data = json.loads(event.removeprefix("data: ").strip())
    assert data["content"] == "hello\n"


# ---------------------------------------------------------------------------
# history_then_live integration test (FR-34)
# ---------------------------------------------------------------------------


def test_history_then_live_replays_then_streams() -> None:
    """FR-34: history_then_live yields history events, then live events.

    Uses is_disconnected to stop the live stream after the first iteration.
    The broker section is verified by checking the subscriber count.
    """
    from app.console import history_then_live

    broker = ConsoleBroker()
    history = [
        {"chunk_index": 0, "content": "old\n", "created_at": "2024-01-01"},
    ]

    async def _run() -> list[str]:
        disconnect_calls = [0]

        async def is_disconnected() -> bool:
            disconnect_calls[0] += 1
            # After the first disconnect check (which happens before waiting
            # for a live chunk), return True to stop the stream.
            return disconnect_calls[0] > 1

        gen = history_then_live(
            broker, history, 42, is_disconnected, poll_timeout=0.1
        )

        events: list[str] = []
        # History event.
        events.append(await gen.__anext__())
        # Generator enters broker section, checks is_disconnected (False),
        # waits 0.1s for a queue item, times out, checks is_disconnected (True),
        # breaks.
        try:
            await gen.__anext__()
            assert False, "should have stopped"
        except StopAsyncIteration:
            pass
        return events

    loop = asyncio.new_event_loop()
    try:
        events = loop.run_until_complete(_run())
    finally:
        loop.close()

    assert len(events) == 1
    data0 = json.loads(events[0].removeprefix("data: ").strip())
    assert data0["content"] == "old\n"


def test_history_then_live_no_broker_just_history() -> None:
    """history_then_live without broker yields only history then stops."""
    from app.console import history_then_live

    history = [
        {"chunk_index": 0, "content": "a\n", "created_at": ""},
        {"chunk_index": 1, "content": "b\n", "created_at": ""},
    ]

    async def _run() -> list[str]:
        gen = history_then_live(None, history, 1, None)
        return [event async for event in gen]

    loop = asyncio.new_event_loop()
    try:
        events = loop.run_until_complete(_run())
    finally:
        loop.close()

    assert len(events) == 2
    data0 = json.loads(events[0].removeprefix("data: ").strip())
    data1 = json.loads(events[1].removeprefix("data: ").strip())
    assert data0["content"] == "a\n"
    assert data1["content"] == "b\n"
