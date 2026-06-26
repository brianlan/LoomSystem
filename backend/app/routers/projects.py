import sqlite3
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request

from app import cleanup_service as cleanup_svc
from app import repositories as repos
from app import schemas
from app.dependencies import get_db_conn
from app.docker import DockerAdapter, SubprocessDockerAdapter

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def _get_docker_adapter(request: Request) -> DockerAdapter:
    adapter = getattr(request.app.state, "docker_adapter", None)
    if adapter is None:
        adapter = SubprocessDockerAdapter()
        request.app.state.docker_adapter = adapter
    return adapter


def _raise_repository_error(exc: repos.RepositoryError) -> NoReturn:
    msg = str(exc)
    if "already exists" in msg:
        raise HTTPException(status_code=409, detail=msg)
    if "Project" in msg and "not found" in msg:
        raise HTTPException(status_code=404, detail=msg)
    raise HTTPException(status_code=422, detail=msg)


@router.post("", response_model=schemas.ProjectRead, status_code=201)
def create_project(
    data: schemas.ProjectCreate,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.Project:
    try:
        return repos.project_create(
            conn,
            name=data.name,
            repo_url=data.repo_url,
            reviewer_config=data.reviewer_config.model_dump() if data.reviewer_config else None,
            implementor_config=data.implementor_config.model_dump()
            if data.implementor_config
            else None,
        )
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)


@router.get("", response_model=list[schemas.ProjectRead])
def list_projects(
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[repos.Project]:
    return repos.project_list(conn)


@router.get("/{project_id}", response_model=schemas.ProjectRead)
def get_project(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.Project:
    project = repos.project_get(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=schemas.ProjectRead)
def update_project(
    project_id: int,
    data: schemas.ProjectUpdate,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> repos.Project:
    try:
        return repos.project_update(
            conn,
            project_id,
            name=data.name,
            repo_url=data.repo_url,
            reviewer_config=data.reviewer_config.model_dump() if data.reviewer_config else None,
            implementor_config=data.implementor_config.model_dump()
            if data.implementor_config
            else None,
        )
    except repos.RepositoryError as exc:
        _raise_repository_error(exc)


@router.delete("/{project_id}", response_model=schemas.MessageResponse)
def delete_project(
    project_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    try:
        cleanup_svc.delete_project_cascade(
            conn, project_id, _get_docker_adapter(request)
        )
    except cleanup_svc.CleanupError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=422, detail=msg) from exc
    return {"message": "Project deleted"}


@router.post(
    "/{project_id}/hard-kill-implementors", response_model=schemas.MessageResponse
)
def hard_kill_implementors(
    project_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    try:
        result = cleanup_svc.hard_kill_implementors(
            conn, project_id, _get_docker_adapter(request)
        )
    except cleanup_svc.CleanupError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=422, detail=msg) from exc
    return {"message": f"Hard-killed {len(result.killed_instance_ids)} implementor(s)"}
