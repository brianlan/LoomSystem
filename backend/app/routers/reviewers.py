"""Reviewer lifecycle API routes (T08).

Thin wrappers over reviewer_service functions. The DockerAdapter and
TriggerService are injected via app.state so tests can override them.
"""

import sqlite3
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request

from app import reviewer_service as svc
from app import schemas
from app.dependencies import get_db_conn
from app.docker import DockerAdapter, SubprocessDockerAdapter
from app.trigger import TriggerService

router = APIRouter(prefix="/api/v1/projects/{project_id}/reviewers", tags=["reviewers"])


def _get_docker_adapter(request: Request) -> DockerAdapter:
    adapter = getattr(request.app.state, "docker_adapter", None)
    if adapter is None:
        adapter = SubprocessDockerAdapter()
        request.app.state.docker_adapter = adapter
    return adapter


def _get_trigger_service(request: Request) -> TriggerService:
    service = getattr(request.app.state, "trigger_service", None)
    if service is None:
        broker = getattr(request.app.state, "console_broker", None)
        service = TriggerService(
            _get_docker_adapter(request), console_broker=broker
        )
        request.app.state.trigger_service = service
    return service


def _raise_reviewer_error(exc: svc.ReviewerError) -> NoReturn:
    msg = str(exc)
    if "not found" in msg.lower():
        raise HTTPException(status_code=404, detail=msg) from exc
    if "cap" in msg.lower():
        raise HTTPException(status_code=409, detail=msg) from exc
    raise HTTPException(status_code=422, detail=msg) from exc


@router.post("/launch", response_model=schemas.ReviewerLaunchResult, status_code=201)
def launch_reviewer(
    project_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, object]:
    try:
        result = svc.launch_reviewer(conn, project_id, _get_docker_adapter(request))
    except svc.ReviewerError as exc:
        _raise_reviewer_error(exc)
    return {
        "agent_instance_id": result.agent_instance_id,
        "container_id": result.container_id,
        "container_name": result.container_name,
    }


@router.post("/{instance_id}/trigger", response_model=schemas.MessageResponse)
def trigger_reviewer(
    project_id: int,
    instance_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    try:
        svc.trigger_reviewer(conn, project_id, _get_trigger_service(request))
    except svc.ReviewerError as exc:
        _raise_reviewer_error(exc)
    return {"message": f"Trigger completed for reviewer {instance_id}"}


@router.post("/{instance_id}/terminate", response_model=schemas.MessageResponse)
def terminate_reviewer(
    project_id: int,
    instance_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    try:
        svc.terminate_reviewer(conn, instance_id, _get_docker_adapter(request))
    except svc.ReviewerError as exc:
        _raise_reviewer_error(exc)
    return {"message": f"Reviewer {instance_id} terminated"}


@router.get("/status", response_model=schemas.ReviewerStatus)
def reviewer_status(
    project_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict[str, object]:
    try:
        return svc.get_reviewer_status(conn, project_id)
    except svc.ReviewerError as exc:
        _raise_reviewer_error(exc)
