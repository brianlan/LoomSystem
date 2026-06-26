import sqlite3
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException

from app import repositories as repos
from app.db import dumps, loads
from app.dependencies import get_db_conn
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionRead,
    AgentDefinitionUpdate,
    DockerImageCreate,
    DockerImageRead,
    DockerImageUpdate,
    GitHubTokenPayload,
    MessageResponse,
    ModelEntryCreate,
    ModelEntryRead,
    ModelEntryUpdate,
    ProxyPayload,
    SSHKeyPayload,
    TriageConfigPayload,
    TriageConfigRead,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_REDACTED = "***"


def _raise_repository_error(exc: repos.RepositoryError) -> NoReturn:
    detail = str(exc)
    if "in use" in detail.lower() or "already exists" in detail.lower():
        raise HTTPException(status_code=409, detail=detail) from exc
    raise HTTPException(status_code=400, detail=detail) from exc


def _project_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM projects LIMIT 1").fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------


@router.post("/agent-definitions", response_model=AgentDefinitionRead, status_code=201)
def create_agent_definition(
    data: AgentDefinitionCreate,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.AgentDefinition:
    try:
        return repos.agent_definition_create(
            conn,
            name=data.name,
            prompt_markdown=data.prompt_markdown,
            github_identity=data.github_identity,
            permissions=data.permissions,
        )
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)


@router.get("/agent-definitions", response_model=list[AgentDefinitionRead])
def list_agent_definitions(
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[repos.AgentDefinition]:
    return repos.agent_definition_list(conn)


@router.get("/agent-definitions/{definition_id}", response_model=AgentDefinitionRead)
def get_agent_definition(
    definition_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.AgentDefinition:
    item = repos.agent_definition_get(conn, definition_id)
    if not item:
        raise HTTPException(status_code=404, detail="Agent definition not found")
    return item


@router.patch("/agent-definitions/{definition_id}", response_model=AgentDefinitionRead)
def update_agent_definition(
    definition_id: int,
    data: AgentDefinitionUpdate,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.AgentDefinition:
    try:
        return repos.agent_definition_update(
            conn,
            definition_id,
            name=data.name,
            prompt_markdown=data.prompt_markdown,
            github_identity=data.github_identity,
            permissions=data.permissions,
        )
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)


@router.delete("/agent-definitions/{definition_id}", response_model=MessageResponse)
def delete_agent_definition(
    definition_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    if not repos.agent_definition_get(conn, definition_id):
        raise HTTPException(status_code=404, detail="Agent definition not found")
    try:
        repos.agent_definition_delete(conn, definition_id)
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)
    return {"message": "Agent definition deleted"}


# ---------------------------------------------------------------------------
# Model entries
# ---------------------------------------------------------------------------


@router.post("/model-entries", response_model=ModelEntryRead, status_code=201)
def create_model_entry(
    data: ModelEntryCreate,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.ModelEntry:
    return repos.model_entry_create(
        conn,
        provider_id=data.provider_id,
        model_id=data.model_id,
        credentials=data.credentials,
        display_name=data.display_name,
        custom_config=data.custom_config,
    )


@router.get("/model-entries", response_model=list[ModelEntryRead])
def list_model_entries(
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[repos.ModelEntry]:
    return repos.model_entry_list(conn)


@router.get("/model-entries/{entry_id}", response_model=ModelEntryRead)
def get_model_entry(
    entry_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.ModelEntry:
    item = repos.model_entry_get(conn, entry_id)
    if not item:
        raise HTTPException(status_code=404, detail="Model entry not found")
    return item


@router.patch("/model-entries/{entry_id}", response_model=ModelEntryRead)
def update_model_entry(
    entry_id: int,
    data: ModelEntryUpdate,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.ModelEntry:
    try:
        return repos.model_entry_update(
            conn,
            entry_id,
            provider_id=data.provider_id,
            model_id=data.model_id,
            credentials=data.credentials,
            display_name=data.display_name,
            custom_config=data.custom_config,
        )
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)


@router.delete("/model-entries/{entry_id}", response_model=MessageResponse)
def delete_model_entry(
    entry_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    if not repos.model_entry_get(conn, entry_id):
        raise HTTPException(status_code=404, detail="Model entry not found")
    try:
        repos.model_entry_delete(conn, entry_id)
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)
    return {"message": "Model entry deleted"}


# ---------------------------------------------------------------------------
# Docker images
# ---------------------------------------------------------------------------


@router.post("/docker-images", response_model=DockerImageRead, status_code=201)
def create_docker_image(
    data: DockerImageCreate,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.DockerImage:
    try:
        return repos.docker_image_create(conn, image_name=data.image_name)
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)


@router.get("/docker-images", response_model=list[DockerImageRead])
def list_docker_images(
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[repos.DockerImage]:
    return repos.docker_image_list(conn)


@router.get("/docker-images/{image_id}", response_model=DockerImageRead)
def get_docker_image(
    image_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.DockerImage:
    item = repos.docker_image_get(conn, image_id)
    if not item:
        raise HTTPException(status_code=404, detail="Docker image not found")
    return item


@router.patch("/docker-images/{image_id}", response_model=DockerImageRead)
def update_docker_image(
    image_id: int,
    data: DockerImageUpdate,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.DockerImage:
    try:
        return repos.docker_image_update(conn, image_id, image_name=data.image_name)
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)


@router.delete("/docker-images/{image_id}", response_model=MessageResponse)
def delete_docker_image(
    image_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    if not repos.docker_image_get(conn, image_id):
        raise HTTPException(status_code=404, detail="Docker image not found")
    try:
        repos.docker_image_delete(conn, image_id)
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)
    return {"message": "Docker image deleted"}


# ---------------------------------------------------------------------------
# Singleton settings
# ---------------------------------------------------------------------------


@router.put("/ssh-key", response_model=MessageResponse)
def set_ssh_key(
    data: SSHKeyPayload,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    repos.setting_set(conn, "ssh_key", data.key_value)
    return {"message": "SSH key updated"}


@router.get("/ssh-key")
def get_ssh_key(conn: sqlite3.Connection = Depends(get_db_conn)) -> dict[str, str | None]:
    value = repos.setting_get(conn, "ssh_key")
    if not value:
        raise HTTPException(status_code=404, detail="SSH key not set")
    return {"key_value": _REDACTED}


@router.delete("/ssh-key", response_model=MessageResponse)
def delete_ssh_key(conn: sqlite3.Connection = Depends(get_db_conn)) -> dict[str, str]:
    if _project_exists(conn):
        raise HTTPException(status_code=409, detail="SSH key is in use by a project")
    repos.setting_set(conn, "ssh_key", "")
    return {"message": "SSH key cleared"}


@router.put("/github-token", response_model=MessageResponse)
def set_github_token(
    data: GitHubTokenPayload,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    repos.setting_set(conn, "github_token", data.token_value)
    return {"message": "GitHub token updated"}


@router.get("/github-token")
def get_github_token(conn: sqlite3.Connection = Depends(get_db_conn)) -> dict[str, str | None]:
    value = repos.setting_get(conn, "github_token")
    if not value:
        raise HTTPException(status_code=404, detail="GitHub token not set")
    return {"token_value": _REDACTED}


@router.delete("/github-token", response_model=MessageResponse)
def delete_github_token(conn: sqlite3.Connection = Depends(get_db_conn)) -> dict[str, str]:
    if _project_exists(conn):
        raise HTTPException(status_code=409, detail="GitHub token is in use by a project")
    repos.setting_set(conn, "github_token", "")
    return {"message": "GitHub token cleared"}


@router.put("/triage-config", response_model=MessageResponse)
def set_triage_config(
    data: TriageConfigPayload,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    repos.setting_set(conn, "triage_endpoint_url", data.endpoint_url)
    repos.setting_set(conn, "triage_model_name", data.model_name)
    repos.setting_set(conn, "triage_api_key", data.api_key)
    repos.setting_set(conn, "triage_headers", dumps(data.headers))
    return {"message": "Triage config updated"}


@router.get("/triage-config", response_model=TriageConfigRead)
def get_triage_config(conn: sqlite3.Connection = Depends(get_db_conn)) -> TriageConfigRead:
    endpoint = repos.setting_get(conn, "triage_endpoint_url")
    model = repos.setting_get(conn, "triage_model_name")
    if not endpoint or not model:
        raise HTTPException(status_code=404, detail="Triage config not set")
    headers = loads(repos.setting_get(conn, "triage_headers")) or {}
    return TriageConfigRead(endpoint_url=endpoint, model_name=model, headers=headers)


@router.delete("/triage-config", response_model=MessageResponse)
def delete_triage_config(conn: sqlite3.Connection = Depends(get_db_conn)) -> dict[str, str]:
    if _project_exists(conn):
        raise HTTPException(status_code=409, detail="Triage config is in use by a project")
    for key in ("triage_endpoint_url", "triage_model_name", "triage_api_key", "triage_headers"):
        repos.setting_set(conn, key, "")
    return {"message": "Triage config cleared"}


@router.put("/proxy", response_model=MessageResponse)
def set_proxy(
    data: ProxyPayload,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    repos.setting_set(conn, "http_proxy", data.http_proxy or "")
    repos.setting_set(conn, "https_proxy", data.https_proxy or "")
    return {"message": "Proxy updated"}


@router.get("/proxy")
def get_proxy(conn: sqlite3.Connection = Depends(get_db_conn)) -> dict[str, str | None]:
    return {
        "http_proxy": repos.setting_get(conn, "http_proxy") or None,
        "https_proxy": repos.setting_get(conn, "https_proxy") or None,
    }


@router.delete("/proxy", response_model=MessageResponse)
def delete_proxy(conn: sqlite3.Connection = Depends(get_db_conn)) -> dict[str, str]:
    if _project_exists(conn):
        raise HTTPException(status_code=409, detail="Proxy is in use by a project")
    repos.setting_set(conn, "http_proxy", "")
    repos.setting_set(conn, "https_proxy", "")
    return {"message": "Proxy cleared"}
