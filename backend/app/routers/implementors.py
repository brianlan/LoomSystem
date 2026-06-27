"""Implementor lifecycle HTTP API routes (T17).

Thin wrappers over the existing implementor service and loop orchestration.
The DockerAdapter is injected via app.state so tests can override it.
"""

from __future__ import annotations

import sqlite3
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request

from app import cleanup_service as cleanup_svc
from app import implementor_loop as loop
from app import implementor_service as svc
from app import repositories as repos
from app import schemas
from app.dependencies import get_db_conn
from app.docker import DockerAdapter, SubprocessDockerAdapter
from app.implementor_service import ImplementorLauncherImpl

router = APIRouter(prefix="/api/v1/projects/{project_id}/implementors", tags=["implementors"])


def _get_docker_adapter(request: Request) -> DockerAdapter:
    adapter = getattr(request.app.state, "docker_adapter", None)
    if adapter is None:
        adapter = SubprocessDockerAdapter()
        request.app.state.docker_adapter = adapter
    return adapter


def _get_launcher(request: Request) -> ImplementorLauncherImpl:
    return ImplementorLauncherImpl(_get_docker_adapter(request))


def _raise_implementor_error(exc: svc.ImplementorError) -> NoReturn:
    msg = str(exc)
    if "not found" in msg.lower():
        raise HTTPException(status_code=404, detail=msg) from exc
    raise HTTPException(status_code=422, detail=msg) from exc


def _raise_loop_error(exc: loop.ImplementorLoopError) -> NoReturn:
    msg = str(exc)
    if "not found" in msg.lower():
        raise HTTPException(status_code=404, detail=msg) from exc
    raise HTTPException(status_code=409, detail=msg) from exc


def _ensure_project(conn: sqlite3.Connection, project_id: int) -> None:
    if repos.project_get(conn, project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")


@router.post("", response_model=schemas.ImplementorLaunchResult, status_code=201)
def launch_implementor(
    project_id: int,
    data: schemas.ImplementorLaunchRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, object]:
    _ensure_project(conn, project_id)
    try:
        result = svc.launch_implementor(
            conn, project_id, data.issue_number, _get_docker_adapter(request)
        )
    except svc.ImplementorError as exc:
        _raise_implementor_error(exc)
    return {
        "agent_instance_id": result.agent_instance_id,
        "container_id": result.container_id,
        "container_name": result.container_name,
    }


@router.post("/{instance_id}/terminate", response_model=schemas.MessageResponse)
def terminate_implementor(
    project_id: int,
    instance_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    _ensure_project(conn, project_id)
    instance = repos.agent_instance_get(conn, instance_id)
    if instance is None or instance.project_id != project_id:
        raise HTTPException(status_code=404, detail="Implementor instance not found")
    if instance.agent_type != "implementor":
        raise HTTPException(status_code=422, detail="Agent instance is not an implementor")
    try:
        svc.terminate_implementor(conn, instance_id, _get_docker_adapter(request))
    except svc.ImplementorError as exc:
        _raise_implementor_error(exc)
    return {"message": f"Implementor {instance_id} terminated"}


@router.post("/loop/start", response_model=schemas.MessageResponse)
def start_loop(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    _ensure_project(conn, project_id)
    try:
        loop.start_loop(conn, project_id)
    except loop.ImplementorLoopError as exc:
        _raise_loop_error(exc)
    return {"message": "Implementor loop started"}


@router.post("/loop/soft-stop", response_model=schemas.MessageResponse)
def soft_stop_loop(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    _ensure_project(conn, project_id)
    loop.soft_stop(conn, project_id)
    return {"message": "Implementor loop soft-stopped (draining)"}


@router.post("/loop/hard-stop", response_model=schemas.MessageResponse)
def hard_stop_loop(
    project_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    _ensure_project(conn, project_id)
    try:
        loop.hard_stop(conn, project_id, _get_launcher(request))
    except svc.ImplementorError as exc:
        _raise_implementor_error(exc)
    return {"message": "Implementor loop hard-stopped"}


@router.get("/status", response_model=schemas.ImplementorStatus)
def implementor_status(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, object]:
    _ensure_project(conn, project_id)
    status = loop.get_status(conn, project_id)
    instances = repos.agent_instance_list_for_project(conn, project_id)
    implementors = [
        {
            "agent_instance_id": inst.id,
            "issue_number": inst.issue_number,
            "container_id": inst.container_id,
            "container_name": inst.container_name,
            "status": inst.status,
        }
        for inst in instances
        if inst.agent_type == "implementor"
    ]
    return {
        "project_id": project_id,
        "state": status["state"],
        "running_implementors": status["running_implementors"],
        "implementors": implementors,
    }


# Preserve backwards-compatible project-level hard-kill endpoint (T12).
# The loop-level hard-stop above is the preferred T17 control surface.
@router.post("/hard-kill", response_model=schemas.MessageResponse)
def hard_kill_implementors(
    project_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    _ensure_project(conn, project_id)
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
