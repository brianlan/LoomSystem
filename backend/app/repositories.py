from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.db import loads


class RepositoryError(Exception):
    pass


def _rowid(cursor: sqlite3.Cursor) -> int:
    assert cursor.lastrowid is not None
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def setting_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def setting_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                       updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@dataclass
class Project:
    id: int
    name: str
    repo_url: str
    reviewer_config: dict[str, Any]
    implementor_config: dict[str, Any]


def project_create(conn: sqlite3.Connection, name: str, repo_url: str) -> Project:
    try:
        cursor = conn.execute(
            "INSERT INTO projects (name, repo_url) VALUES (?, ?)",
            (name, repo_url),
        )
    except sqlite3.IntegrityError as exc:
        raise RepositoryError(f"Project '{name}' already exists") from exc
    return Project(
        id=_rowid(cursor),
        name=name,
        repo_url=repo_url,
        reviewer_config={},
        implementor_config={},
    )


def project_get(conn: sqlite3.Connection, project_id: int) -> Project | None:
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        return None
    return Project(
        id=row["id"],
        name=row["name"],
        repo_url=row["repo_url"],
        reviewer_config=loads(row["reviewer_config_json"]) or {},
        implementor_config=loads(row["implementor_config_json"]) or {},
    )


def project_list(conn: sqlite3.Connection) -> list[Project]:
    rows = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
    return [
        Project(
            id=row["id"],
            name=row["name"],
            repo_url=row["repo_url"],
            reviewer_config=loads(row["reviewer_config_json"]) or {},
            implementor_config=loads(row["implementor_config_json"]) or {},
        )
        for row in rows
    ]


def project_update_config(
    conn: sqlite3.Connection,
    project_id: int,
    reviewer_config: dict[str, Any] | None = None,
    implementor_config: dict[str, Any] | None = None,
) -> None:
    project = project_get(conn, project_id)
    if not project:
        raise RepositoryError(f"Project {project_id} not found")
    reviewer = reviewer_config if reviewer_config is not None else project.reviewer_config
    implementor = (
        implementor_config if implementor_config is not None else project.implementor_config
    )
    conn.execute(
        """
        UPDATE projects
        SET reviewer_config_json = ?,
            implementor_config_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (json.dumps(reviewer), json.dumps(implementor), project_id),
    )


def project_delete(conn: sqlite3.Connection, project_id: int) -> None:
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------


@dataclass
class AgentDefinition:
    id: int
    name: str
    prompt_markdown: str
    github_identity: str
    permissions: dict[str, Any]


def agent_definition_create(
    conn: sqlite3.Connection,
    name: str,
    prompt_markdown: str,
    github_identity: str,
    permissions: dict[str, Any] | None = None,
) -> AgentDefinition:
    try:
        cursor = conn.execute(
            """
            INSERT INTO agent_definitions (name, prompt_markdown, github_identity, permissions_json)
            VALUES (?, ?, ?, ?)
            """,
            (name, prompt_markdown, github_identity, json.dumps(permissions or {})),
        )
    except sqlite3.IntegrityError as exc:
        raise RepositoryError(f"Agent definition '{name}' already exists") from exc
    return AgentDefinition(
        id=_rowid(cursor),
        name=name,
        prompt_markdown=prompt_markdown,
        github_identity=github_identity,
        permissions=permissions or {},
    )


def agent_definition_get(conn: sqlite3.Connection, definition_id: int) -> AgentDefinition | None:
    row = conn.execute(
        "SELECT * FROM agent_definitions WHERE id = ?", (definition_id,)
    ).fetchone()
    if not row:
        return None
    return AgentDefinition(
        id=row["id"],
        name=row["name"],
        prompt_markdown=row["prompt_markdown"],
        github_identity=row["github_identity"],
        permissions=loads(row["permissions_json"]) or {},
    )


def agent_definition_list(conn: sqlite3.Connection) -> list[AgentDefinition]:
    rows = conn.execute("SELECT * FROM agent_definitions ORDER BY id").fetchall()
    return [
        AgentDefinition(
            id=row["id"],
            name=row["name"],
            prompt_markdown=row["prompt_markdown"],
            github_identity=row["github_identity"],
            permissions=loads(row["permissions_json"]) or {},
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Model entries
# ---------------------------------------------------------------------------


@dataclass
class ModelEntry:
    id: int
    provider_id: str
    model_id: str
    display_name: str | None
    custom_config: dict[str, Any] | None
    credentials: str


def model_entry_create(
    conn: sqlite3.Connection,
    provider_id: str,
    model_id: str,
    credentials: str,
    display_name: str | None = None,
    custom_config: dict[str, Any] | None = None,
) -> ModelEntry:
    cursor = conn.execute(
        """
        INSERT INTO model_entries
        (provider_id, model_id, display_name, custom_config_json, credentials)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            provider_id,
            model_id,
            display_name,
            json.dumps(custom_config) if custom_config else None,
            credentials,
        ),
    )
    return ModelEntry(
        id=_rowid(cursor),
        provider_id=provider_id,
        model_id=model_id,
        display_name=display_name,
        custom_config=custom_config,
        credentials=credentials,
    )


def model_entry_get(conn: sqlite3.Connection, entry_id: int) -> ModelEntry | None:
    row = conn.execute("SELECT * FROM model_entries WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        return None
    return ModelEntry(
        id=row["id"],
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        display_name=row["display_name"],
        custom_config=loads(row["custom_config_json"]),
        credentials=row["credentials"],
    )


# ---------------------------------------------------------------------------
# Docker images
# ---------------------------------------------------------------------------


@dataclass
class DockerImage:
    id: int
    image_name: str


def docker_image_create(conn: sqlite3.Connection, image_name: str) -> DockerImage:
    try:
        cursor = conn.execute(
            "INSERT INTO docker_images (image_name) VALUES (?)",
            (image_name,),
        )
    except sqlite3.IntegrityError as exc:
        raise RepositoryError(f"Docker image '{image_name}' already exists") from exc
    return DockerImage(id=_rowid(cursor), image_name=image_name)


def docker_image_get(conn: sqlite3.Connection, image_id: int) -> DockerImage | None:
    row = conn.execute("SELECT * FROM docker_images WHERE id = ?", (image_id,)).fetchone()
    if not row:
        return None
    return DockerImage(id=row["id"], image_name=row["image_name"])


# ---------------------------------------------------------------------------
# Agent instances
# ---------------------------------------------------------------------------


@dataclass
class AgentInstance:
    id: int
    project_id: int
    agent_type: str
    agent_definition_id: int | None
    model_entry_id: int | None
    docker_image_id: int | None
    issue_number: int | None
    container_id: str | None
    session_id: str | None
    status: str


def agent_instance_create(
    conn: sqlite3.Connection,
    project_id: int,
    agent_type: str,
    agent_definition_id: int | None = None,
    model_entry_id: int | None = None,
    docker_image_id: int | None = None,
    issue_number: int | None = None,
    container_id: str | None = None,
    session_id: str | None = None,
) -> AgentInstance:
    cursor = conn.execute(
        """
        INSERT INTO agent_instances
        (project_id, agent_type, agent_definition_id, model_entry_id, docker_image_id,
         issue_number, container_id, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            agent_type,
            agent_definition_id,
            model_entry_id,
            docker_image_id,
            issue_number,
            container_id,
            session_id,
        ),
    )
    return AgentInstance(
        id=_rowid(cursor),
        project_id=project_id,
        agent_type=agent_type,
        agent_definition_id=agent_definition_id,
        model_entry_id=model_entry_id,
        docker_image_id=docker_image_id,
        issue_number=issue_number,
        container_id=container_id,
        session_id=session_id,
        status="running",
    )


def agent_instance_get(conn: sqlite3.Connection, instance_id: int) -> AgentInstance | None:
    row = conn.execute(
        "SELECT * FROM agent_instances WHERE id = ?", (instance_id,)
    ).fetchone()
    if not row:
        return None
    return AgentInstance(
        id=row["id"],
        project_id=row["project_id"],
        agent_type=row["agent_type"],
        agent_definition_id=row["agent_definition_id"],
        model_entry_id=row["model_entry_id"],
        docker_image_id=row["docker_image_id"],
        issue_number=row["issue_number"],
        container_id=row["container_id"],
        session_id=row["session_id"],
        status=row["status"],
    )


def agent_instance_update(
    conn: sqlite3.Connection,
    instance_id: int,
    **kwargs: Any,
) -> None:
    allowed = {
        "agent_definition_id",
        "model_entry_id",
        "docker_image_id",
        "issue_number",
        "container_id",
        "session_id",
        "status",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [instance_id]
    conn.execute(
        f"UPDATE agent_instances SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        values,
    )


def agent_instance_list_for_project(
    conn: sqlite3.Connection, project_id: int
) -> list[AgentInstance]:
    rows = conn.execute(
        "SELECT * FROM agent_instances WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    return [
        AgentInstance(
            id=row["id"],
            project_id=row["project_id"],
            agent_type=row["agent_type"],
            agent_definition_id=row["agent_definition_id"],
            model_entry_id=row["model_entry_id"],
            docker_image_id=row["docker_image_id"],
            issue_number=row["issue_number"],
            container_id=row["container_id"],
            session_id=row["session_id"],
            status=row["status"],
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Config snapshots
# ---------------------------------------------------------------------------


def config_snapshot_create(
    conn: sqlite3.Connection, agent_instance_id: int, snapshot: dict[str, Any]
) -> int:
    cursor = conn.execute(
        "INSERT INTO config_snapshots (agent_instance_id, snapshot_json) VALUES (?, ?)",
        (agent_instance_id, json.dumps(snapshot)),
    )
    return _rowid(cursor)


def config_snapshot_get(
    conn: sqlite3.Connection, agent_instance_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT snapshot_json FROM config_snapshots WHERE agent_instance_id = ?",
        (agent_instance_id,),
    ).fetchone()
    return loads(row["snapshot_json"]) if row else None


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


@dataclass
class Trigger:
    id: int
    agent_instance_id: int
    started_at: str
    ended_at: str | None
    exit_code: int | None
    output: str | None


def trigger_create(conn: sqlite3.Connection, agent_instance_id: int) -> Trigger:
    cursor = conn.execute(
        "INSERT INTO triggers (agent_instance_id) VALUES (?)",
        (agent_instance_id,),
    )
    row = conn.execute("SELECT * FROM triggers WHERE id = ?", (_rowid(cursor),)).fetchone()
    return Trigger(
        id=row["id"],
        agent_instance_id=row["agent_instance_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        exit_code=row["exit_code"],
        output=row["output"],
    )


def trigger_finish(
    conn: sqlite3.Connection,
    trigger_id: int,
    exit_code: int,
    output: str,
) -> None:
    conn.execute(
        """
        UPDATE triggers
        SET ended_at = CURRENT_TIMESTAMP, exit_code = ?, output = ?
        WHERE id = ?
        """,
        (exit_code, output, trigger_id),
    )


# ---------------------------------------------------------------------------
# Console chunks
# ---------------------------------------------------------------------------


def console_chunk_append(
    conn: sqlite3.Connection, agent_instance_id: int, chunk_index: int, content: str
) -> None:
    conn.execute(
        """
        INSERT INTO console_chunks (agent_instance_id, chunk_index, content)
        VALUES (?, ?, ?)
        ON CONFLICT(agent_instance_id, chunk_index) DO UPDATE SET content = excluded.content
        """,
        (agent_instance_id, chunk_index, content),
    )


def console_chunk_list(
    conn: sqlite3.Connection, agent_instance_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT chunk_index, content, created_at FROM console_chunks
        WHERE agent_instance_id = ?
        ORDER BY chunk_index
        """,
        (agent_instance_id,),
    ).fetchall()
    return [
        {
            "chunk_index": row["chunk_index"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


def audit_event_create(
    conn: sqlite3.Connection,
    event_type: str,
    project_id: int | None = None,
    agent_instance_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO audit_events (project_id, agent_instance_id, event_type, payload_json)
        VALUES (?, ?, ?, ?)
        """,
        (project_id, agent_instance_id, event_type, json.dumps(payload or {})),
    )
    return _rowid(cursor)


def audit_event_list(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    agent_instance_id: int | None = None,
) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if project_id is not None:
        where.append("project_id = ?")
        params.append(project_id)
    if agent_instance_id is not None:
        where.append("agent_instance_id = ?")
        params.append(agent_instance_id)
    rows = conn.execute(
        f"SELECT * FROM audit_events WHERE {' AND '.join(where)} ORDER BY id",
        params,
    ).fetchall()
    return [
        {
            "id": row["id"],
            "project_id": row["project_id"],
            "agent_instance_id": row["agent_instance_id"],
            "event_type": row["event_type"],
            "payload": loads(row["payload_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Triage runs
# ---------------------------------------------------------------------------


def triage_run_create(
    conn: sqlite3.Connection, project_id: int, ranked_issue_ids: list[int]
) -> int:
    cursor = conn.execute(
        "INSERT INTO triage_runs (project_id, ranked_issue_ids_json) VALUES (?, ?)",
        (project_id, json.dumps(ranked_issue_ids)),
    )
    return _rowid(cursor)


def triage_run_latest(
    conn: sqlite3.Connection, project_id: int
) -> list[int] | None:
    row = conn.execute(
        """
        SELECT ranked_issue_ids_json FROM triage_runs
        WHERE project_id = ? ORDER BY id DESC LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return loads(row["ranked_issue_ids_json"]) if row else None


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def notification_create(
    conn: sqlite3.Connection,
    message: str,
    project_id: int | None = None,
    agent_instance_id: int | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO notifications (project_id, agent_instance_id, message)
        VALUES (?, ?, ?)
        """,
        (project_id, agent_instance_id, message),
    )
    return _rowid(cursor)


def notification_list(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if project_id is not None:
        where.append("project_id = ?")
        params.append(project_id)
    if unread_only:
        where.append("is_read = 0")
    rows = conn.execute(
        f"SELECT * FROM notifications WHERE {' AND '.join(where)} ORDER BY id",
        params,
    ).fetchall()
    return [
        {
            "id": row["id"],
            "project_id": row["project_id"],
            "agent_instance_id": row["agent_instance_id"],
            "message": row["message"],
            "is_read": bool(row["is_read"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def notification_mark_read(conn: sqlite3.Connection, notification_id: int) -> None:
    conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ?",
        (notification_id,),
    )
