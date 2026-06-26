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


DEFAULT_REVIEWER_INTERVAL_MINUTES = 15
DEFAULT_IMPLEMENTOR_INTERVAL_MINUTES = 15
DEFAULT_REVIEWER_CAP = 1
DEFAULT_IMPLEMENTOR_PARALLELISM = 1


@dataclass
class Project:
    id: int
    name: str
    repo_url: str
    reviewer_config: dict[str, Any]
    implementor_config: dict[str, Any]
    created_at: str
    updated_at: str


def _default_reviewer_config() -> dict[str, Any]:
    return {
        "trigger_interval_minutes": DEFAULT_REVIEWER_INTERVAL_MINUTES,
        "reviewer_cap": DEFAULT_REVIEWER_CAP,
    }


def _default_implementor_config() -> dict[str, Any]:
    return {
        "trigger_interval_minutes": DEFAULT_IMPLEMENTOR_INTERVAL_MINUTES,
        "parallelism": DEFAULT_IMPLEMENTOR_PARALLELISM,
    }


def _project_from_row(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        repo_url=row["repo_url"],
        reviewer_config=loads(row["reviewer_config_json"]) or {},
        implementor_config=loads(row["implementor_config_json"]) or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _validate_positive_int(value: Any, name: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise RepositoryError(f"{name} must be a positive integer")


def _validate_reference_exists(
    conn: sqlite3.Connection, table: str, entity_id: int, name: str
) -> None:
    row = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        raise RepositoryError(f"{name} {entity_id} does not exist")


def _validate_project_config(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    agent_type: str,
) -> None:
    ref_keys = ("agent_definition_id", "model_entry_id", "docker_image_id")
    has_refs = any(key in config for key in ref_keys)
    if has_refs:
        for key in ref_keys:
            value = config.get(key)
            if value is None:
                raise RepositoryError(f"{agent_type} {key} is required")
            if not isinstance(value, int):
                raise RepositoryError(f"{agent_type} {key} must be an integer")
        _validate_reference_exists(
            conn, "agent_definitions", config["agent_definition_id"], "Agent definition"
        )
        _validate_reference_exists(conn, "model_entries", config["model_entry_id"], "Model entry")
        _validate_reference_exists(conn, "docker_images", config["docker_image_id"], "Docker image")
    if "trigger_interval_minutes" in config:
        _validate_positive_int(config["trigger_interval_minutes"], "trigger_interval_minutes")
    if agent_type == "reviewer" and "reviewer_cap" in config:
        _validate_positive_int(config["reviewer_cap"], "reviewer_cap")
    if agent_type == "implementor" and "parallelism" in config:
        _validate_positive_int(config["parallelism"], "parallelism")


def project_create(
    conn: sqlite3.Connection,
    name: str,
    repo_url: str,
    reviewer_config: dict[str, Any] | None = None,
    implementor_config: dict[str, Any] | None = None,
) -> Project:
    if reviewer_config is not None:
        _validate_project_config(conn, reviewer_config, "reviewer")
    else:
        reviewer_config = _default_reviewer_config()
    if implementor_config is not None:
        _validate_project_config(conn, implementor_config, "implementor")
    else:
        implementor_config = _default_implementor_config()
    try:
        cursor = conn.execute(
            """
            INSERT INTO projects (name, repo_url, reviewer_config_json, implementor_config_json)
            VALUES (?, ?, ?, ?)
            """,
            (name, repo_url, json.dumps(reviewer_config), json.dumps(implementor_config)),
        )
    except sqlite3.IntegrityError as exc:
        raise RepositoryError(f"Project '{name}' already exists") from exc
    project = project_get(conn, _rowid(cursor))
    assert project is not None
    return project


def project_get(conn: sqlite3.Connection, project_id: int) -> Project | None:
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        return None
    return _project_from_row(row)


def project_list(conn: sqlite3.Connection) -> list[Project]:
    rows = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
    return [_project_from_row(row) for row in rows]


def project_update(
    conn: sqlite3.Connection,
    project_id: int,
    name: str | None = None,
    repo_url: str | None = None,
    reviewer_config: dict[str, Any] | None = None,
    implementor_config: dict[str, Any] | None = None,
) -> Project:
    project = project_get(conn, project_id)
    if not project:
        raise RepositoryError(f"Project {project_id} not found")
    new_name = name if name is not None else project.name
    new_repo_url = repo_url if repo_url is not None else project.repo_url
    new_reviewer = reviewer_config if reviewer_config is not None else project.reviewer_config
    new_implementor = (
        implementor_config if implementor_config is not None else project.implementor_config
    )
    _validate_project_config(conn, new_reviewer, "reviewer")
    _validate_project_config(conn, new_implementor, "implementor")
    try:
        conn.execute(
            """
            UPDATE projects
            SET name = ?, repo_url = ?, reviewer_config_json = ?, implementor_config_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                new_name,
                new_repo_url,
                json.dumps(new_reviewer),
                json.dumps(new_implementor),
                project_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise RepositoryError(f"Project '{new_name}' already exists") from exc
    updated = project_get(conn, project_id)
    assert updated is not None
    return updated


def project_update_config(
    conn: sqlite3.Connection,
    project_id: int,
    reviewer_config: dict[str, Any] | None = None,
    implementor_config: dict[str, Any] | None = None,
) -> None:
    project_update(
        conn,
        project_id,
        reviewer_config=reviewer_config,
        implementor_config=implementor_config,
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


def agent_definition_update(
    conn: sqlite3.Connection,
    definition_id: int,
    name: str | None = None,
    prompt_markdown: str | None = None,
    github_identity: str | None = None,
    permissions: dict[str, Any] | None = None,
) -> AgentDefinition:
    existing = agent_definition_get(conn, definition_id)
    if not existing:
        raise RepositoryError(f"Agent definition {definition_id} not found")
    updates: dict[str, Any] = {
        "name": name if name is not None else existing.name,
        "prompt_markdown": (
            prompt_markdown if prompt_markdown is not None else existing.prompt_markdown
        ),
        "github_identity": (
            github_identity if github_identity is not None else existing.github_identity
        ),
        "permissions_json": json.dumps(
            permissions if permissions is not None else existing.permissions
        ),
    }
    try:
        conn.execute(
            """
            UPDATE agent_definitions
            SET name = ?, prompt_markdown = ?, github_identity = ?, permissions_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                updates["name"],
                updates["prompt_markdown"],
                updates["github_identity"],
                updates["permissions_json"],
                definition_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise RepositoryError(f"Agent definition '{updates['name']}' already exists") from exc
    updated = agent_definition_get(conn, definition_id)
    assert updated is not None
    return updated


def agent_definition_delete(conn: sqlite3.Connection, definition_id: int) -> None:
    if agent_definition_in_use(conn, definition_id):
        raise RepositoryError(f"Agent definition {definition_id} is in use")
    conn.execute("DELETE FROM agent_definitions WHERE id = ?", (definition_id,))


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


def model_entry_list(conn: sqlite3.Connection) -> list[ModelEntry]:
    rows = conn.execute("SELECT * FROM model_entries ORDER BY id").fetchall()
    return [
        ModelEntry(
            id=row["id"],
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            display_name=row["display_name"],
            custom_config=loads(row["custom_config_json"]),
            credentials=row["credentials"],
        )
        for row in rows
    ]


def model_entry_update(
    conn: sqlite3.Connection,
    entry_id: int,
    provider_id: str | None = None,
    model_id: str | None = None,
    credentials: str | None = None,
    display_name: str | None = None,
    custom_config: dict[str, Any] | None = None,
) -> ModelEntry:
    existing = model_entry_get(conn, entry_id)
    if not existing:
        raise RepositoryError(f"Model entry {entry_id} not found")
    conn.execute(
        """
        UPDATE model_entries
        SET provider_id = COALESCE(?, provider_id),
            model_id = COALESCE(?, model_id),
            credentials = COALESCE(?, credentials),
            display_name = COALESCE(?, display_name),
            custom_config_json = COALESCE(?, custom_config_json),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            provider_id,
            model_id,
            credentials,
            display_name,
            json.dumps(custom_config) if custom_config is not None else None,
            entry_id,
        ),
    )
    updated = model_entry_get(conn, entry_id)
    assert updated is not None
    return updated


def model_entry_delete(conn: sqlite3.Connection, entry_id: int) -> None:
    if model_entry_in_use(conn, entry_id):
        raise RepositoryError(f"Model entry {entry_id} is in use")
    conn.execute("DELETE FROM model_entries WHERE id = ?", (entry_id,))


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


def docker_image_list(conn: sqlite3.Connection) -> list[DockerImage]:
    rows = conn.execute("SELECT * FROM docker_images ORDER BY id").fetchall()
    return [DockerImage(id=row["id"], image_name=row["image_name"]) for row in rows]


def docker_image_update(
    conn: sqlite3.Connection, image_id: int, image_name: str
) -> DockerImage:
    existing = docker_image_get(conn, image_id)
    if not existing:
        raise RepositoryError(f"Docker image {image_id} not found")
    try:
        conn.execute(
            "UPDATE docker_images SET image_name = ? WHERE id = ?",
            (image_name, image_id),
        )
    except sqlite3.IntegrityError as exc:
        raise RepositoryError(f"Docker image '{image_name}' already exists") from exc
    updated = docker_image_get(conn, image_id)
    assert updated is not None
    return updated


def docker_image_delete(conn: sqlite3.Connection, image_id: int) -> None:
    if docker_image_in_use(conn, image_id):
        raise RepositoryError(f"Docker image {image_id} is in use")
    conn.execute("DELETE FROM docker_images WHERE id = ?", (image_id,))


# ---------------------------------------------------------------------------
# In-use checks
# ---------------------------------------------------------------------------


def _id_referenced_in_json(value: Any, target_id: int) -> bool:
    if isinstance(value, int):
        return value == target_id
    if isinstance(value, dict):
        return any(_id_referenced_in_json(v, target_id) for v in value.values())
    if isinstance(value, list):
        return any(_id_referenced_in_json(item, target_id) for item in value)
    return False


def _entity_in_use(
    conn: sqlite3.Connection,
    entity_id: int,
    entity_column: str,
) -> bool:
    instance_row = conn.execute(
        f"SELECT 1 FROM agent_instances WHERE {entity_column} = ? LIMIT 1", (entity_id,)
    ).fetchone()
    if instance_row is not None:
        return True
    for column in ("reviewer_config_json", "implementor_config_json"):
        rows = conn.execute(
            f"SELECT {column} FROM projects WHERE {column} IS NOT NULL AND {column} != '{{}}'"
        ).fetchall()
        for row in rows:
            if _id_referenced_in_json(loads(row[column]), entity_id):
                return True
    return False


def agent_definition_in_use(conn: sqlite3.Connection, definition_id: int) -> bool:
    return _entity_in_use(conn, definition_id, "agent_definition_id")


def model_entry_in_use(conn: sqlite3.Connection, entry_id: int) -> bool:
    return _entity_in_use(conn, entry_id, "model_entry_id")


def docker_image_in_use(conn: sqlite3.Connection, image_id: int) -> bool:
    return _entity_in_use(conn, image_id, "docker_image_id")


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
    container_name: str | None
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
    container_name: str | None = None,
    session_id: str | None = None,
) -> AgentInstance:
    cursor = conn.execute(
        """
        INSERT INTO agent_instances
        (project_id, agent_type, agent_definition_id, model_entry_id, docker_image_id,
         issue_number, container_id, container_name, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            agent_type,
            agent_definition_id,
            model_entry_id,
            docker_image_id,
            issue_number,
            container_id,
            container_name,
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
        container_name=container_name,
        session_id=session_id,
        status="running",
    )


def _agent_instance_from_row(row: sqlite3.Row) -> AgentInstance:
    return AgentInstance(
        id=row["id"],
        project_id=row["project_id"],
        agent_type=row["agent_type"],
        agent_definition_id=row["agent_definition_id"],
        model_entry_id=row["model_entry_id"],
        docker_image_id=row["docker_image_id"],
        issue_number=row["issue_number"],
        container_id=row["container_id"],
        container_name=row["container_name"],
        session_id=row["session_id"],
        status=row["status"],
    )


def agent_instance_get(conn: sqlite3.Connection, instance_id: int) -> AgentInstance | None:
    row = conn.execute(
        "SELECT * FROM agent_instances WHERE id = ?", (instance_id,)
    ).fetchone()
    if not row:
        return None
    return _agent_instance_from_row(row)


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
        "container_name",
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
    return [_agent_instance_from_row(row) for row in rows]


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
    exit_code: int | None,
    output: str,
) -> None:
    # exit_code is None for an incomplete trigger (lost stream, EC-1/EC-2);
    # a non-None value records success (0) or a failed trigger (non-zero, EC-3).
    conn.execute(
        """
        UPDATE triggers
        SET ended_at = CURRENT_TIMESTAMP, exit_code = ?, output = ?
        WHERE id = ?
        """,
        (exit_code, output, trigger_id),
    )


def trigger_latest_for_instance(
    conn: sqlite3.Connection, agent_instance_id: int
) -> Trigger | None:
    row = conn.execute(
        "SELECT * FROM triggers WHERE agent_instance_id = ? ORDER BY id DESC LIMIT 1",
        (agent_instance_id,),
    ).fetchone()
    return (
        Trigger(
            id=row["id"],
            agent_instance_id=row["agent_instance_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            exit_code=row["exit_code"],
            output=row["output"],
        )
        if row
        else None
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


@dataclass
class TriageRun:
    id: int
    project_id: int
    ranked_issue_ids: list[int]
    input_snapshot: list[dict[str, Any]] | None
    raw_response: str | None
    status: str
    attempts: int
    error: str | None
    created_at: str


def triage_run_create(
    conn: sqlite3.Connection, project_id: int, ranked_issue_ids: list[int]
) -> int:
    cursor = conn.execute(
        "INSERT INTO triage_runs (project_id, ranked_issue_ids_json) VALUES (?, ?)",
        (project_id, json.dumps(ranked_issue_ids)),
    )
    return _rowid(cursor)


def triage_run_create_full(
    conn: sqlite3.Connection,
    project_id: int,
    ranked_issue_ids: list[int],
    input_snapshot: list[dict[str, Any]] | None = None,
    raw_response: str | None = None,
    status: str = "success",
    attempts: int = 1,
    error: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO triage_runs
        (project_id, ranked_issue_ids_json, input_snapshot_json, raw_response,
         status, attempts, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            json.dumps(ranked_issue_ids),
            json.dumps(input_snapshot) if input_snapshot is not None else None,
            raw_response,
            status,
            attempts,
            error,
        ),
    )
    return _rowid(cursor)


def _triage_run_from_row(row: sqlite3.Row) -> TriageRun:
    return TriageRun(
        id=row["id"],
        project_id=row["project_id"],
        ranked_issue_ids=loads(row["ranked_issue_ids_json"]) or [],
        input_snapshot=loads(row["input_snapshot_json"]),
        raw_response=row["raw_response"],
        status=row["status"],
        attempts=row["attempts"],
        error=row["error"],
        created_at=row["created_at"],
    )


def triage_run_get(conn: sqlite3.Connection, run_id: int) -> TriageRun | None:
    row = conn.execute("SELECT * FROM triage_runs WHERE id = ?", (run_id,)).fetchone()
    return _triage_run_from_row(row) if row else None


def triage_run_latest(conn: sqlite3.Connection, project_id: int) -> list[int] | None:
    row = conn.execute(
        """
        SELECT ranked_issue_ids_json FROM triage_runs
        WHERE project_id = ? ORDER BY id DESC LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return loads(row["ranked_issue_ids_json"]) if row else None


def triage_run_latest_full(conn: sqlite3.Connection, project_id: int) -> TriageRun | None:
    row = conn.execute(
        "SELECT * FROM triage_runs WHERE project_id = ? ORDER BY id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return _triage_run_from_row(row) if row else None


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


# ---------------------------------------------------------------------------
# GitHub snapshots and polling status
# ---------------------------------------------------------------------------


# Per-issue LoomSystem status lifecycle (D45: reopened issues re-enter the pool).
ISSUE_STATUS_UNASSIGNED = "unassigned"
ISSUE_STATUS_IN_PROGRESS = "in-progress"
ISSUE_STATUS_PR_OPENED = "PR-opened"
ISSUE_STATUS_RESOLVED = "resolved"
ISSUE_STATUS_FAILED = "failed"


@dataclass
class GitHubIssueSnapshot:
    id: int
    project_id: int
    issue_number: int
    title: str
    state: str
    loom_status: str
    updated_at: str


@dataclass
class GitHubPullRequestSnapshot:
    id: int
    project_id: int
    pr_number: int
    title: str
    state: str
    merged: bool
    updated_at: str


@dataclass
class PollingStatus:
    project_id: int
    last_polled_at: str | None
    last_ok: bool
    last_error: str | None


def _github_issue_from_row(row: sqlite3.Row) -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        id=row["id"],
        project_id=row["project_id"],
        issue_number=row["issue_number"],
        title=row["title"],
        state=row["state"],
        loom_status=row["loom_status"],
        updated_at=row["updated_at"],
    )


def github_issue_list(conn: sqlite3.Connection, project_id: int) -> list[GitHubIssueSnapshot]:
    rows = conn.execute(
        "SELECT * FROM github_issues WHERE project_id = ? ORDER BY issue_number",
        (project_id,),
    ).fetchall()
    return [_github_issue_from_row(row) for row in rows]


def github_issue_get(
    conn: sqlite3.Connection, project_id: int, issue_number: int
) -> GitHubIssueSnapshot | None:
    row = conn.execute(
        "SELECT * FROM github_issues WHERE project_id = ? AND issue_number = ?",
        (project_id, issue_number),
    ).fetchone()
    return _github_issue_from_row(row) if row else None


def github_issue_set_status(
    conn: sqlite3.Connection, project_id: int, issue_number: int, loom_status: str
) -> None:
    conn.execute(
        "UPDATE github_issues SET loom_status = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE project_id = ? AND issue_number = ?",
        (loom_status, project_id, issue_number),
    )


def _github_pr_from_row(row: sqlite3.Row) -> GitHubPullRequestSnapshot:
    return GitHubPullRequestSnapshot(
        id=row["id"],
        project_id=row["project_id"],
        pr_number=row["pr_number"],
        title=row["title"],
        state=row["state"],
        merged=bool(row["merged"]),
        updated_at=row["updated_at"],
    )


def github_pr_list(
    conn: sqlite3.Connection, project_id: int
) -> list[GitHubPullRequestSnapshot]:
    rows = conn.execute(
        "SELECT * FROM github_prs WHERE project_id = ? ORDER BY pr_number",
        (project_id,),
    ).fetchall()
    return [_github_pr_from_row(row) for row in rows]


def github_pr_get(
    conn: sqlite3.Connection, project_id: int, pr_number: int
) -> GitHubPullRequestSnapshot | None:
    row = conn.execute(
        "SELECT * FROM github_prs WHERE project_id = ? AND pr_number = ?",
        (project_id, pr_number),
    ).fetchone()
    return _github_pr_from_row(row) if row else None


def polling_status_get(conn: sqlite3.Connection, project_id: int) -> PollingStatus | None:
    row = conn.execute(
        "SELECT * FROM polling_status WHERE project_id = ?", (project_id,)
    ).fetchone()
    if not row:
        return None
    return PollingStatus(
        project_id=row["project_id"],
        last_polled_at=row["last_polled_at"],
        last_ok=bool(row["last_ok"]),
        last_error=row["last_error"],
    )
