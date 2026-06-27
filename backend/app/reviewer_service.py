"""Reviewer lifecycle service: launch with cap enforcement, manual trigger,
termination, and status projection (T08).

Thin domain layer over the shared launch (T06) and trigger (T07) services.
Reviewer lifetime ends only through explicit termination, project deletion,
or relevant cascade behavior (D38: no pause/resume, only terminate).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import repositories as repos
from app.docker import DockerAdapter
from app.launch import (
    LABEL_INSTANCE,
    LaunchError,
    LaunchSpec,
    cleanup_credential_dir,
    launch_agent,
)
from app.trigger import TriggerOutcome, TriggerService, trigger_request_for_instance

# Default trigger prompt for reviewer agents. The agent definition markdown
# (written to .opencode/agents/<name>.md during launch) provides the system
# prompt; this is the per-trigger task instruction.
# ponytail: hardcoded default; parameterize when reviewer prompts need customization.
_DEFAULT_REVIEWER_PROMPT = "review"


class ReviewerError(Exception):
    """Raised when a reviewer lifecycle operation fails."""


@dataclass(frozen=True)
class ReviewerLaunchResult:
    agent_instance_id: int
    container_id: str
    container_name: str


def _running_reviewer_count(conn: sqlite3.Connection, project_id: int) -> int:
    instances = repos.agent_instance_list_for_project(conn, project_id)
    return sum(
        1
        for inst in instances
        if inst.agent_type == "reviewer" and inst.status == "running"
    )


def _reviewer_cap(project: repos.Project) -> int:
    return int(project.reviewer_config.get("reviewer_cap", repos.DEFAULT_REVIEWER_CAP))


def build_launch_spec(conn: sqlite3.Connection, project: repos.Project) -> LaunchSpec:
    """Build a LaunchSpec from the project's reviewer_config + global settings."""
    config = project.reviewer_config
    agent_def = repos.agent_definition_get(conn, config["agent_definition_id"])
    model = repos.model_entry_get(conn, config["model_entry_id"])
    image = repos.docker_image_get(conn, config["docker_image_id"])
    ssh_key = repos.setting_get(conn, "ssh_key")

    if agent_def is None:
        raise ReviewerError("Reviewer agent definition not found")
    if model is None:
        raise ReviewerError("Reviewer model entry not found")
    if image is None:
        raise ReviewerError("Reviewer docker image not found")
    if not ssh_key:
        raise ReviewerError("SSH key not configured")

    http_proxy = repos.setting_get(conn, "http_proxy") or None
    https_proxy = repos.setting_get(conn, "https_proxy") or None

    return LaunchSpec(
        project_id=project.id,
        project_name=project.name,
        repo_url=project.repo_url,
        agent_type="reviewer",
        agent_definition_id=agent_def.id,
        agent_name=agent_def.name,
        prompt_markdown=agent_def.prompt_markdown,
        github_identity=agent_def.github_identity,
        model_entry_id=model.id,
        model_provider_id=model.provider_id,
        model_id=model.model_id,
        model_credentials=model.credentials,
        model_custom_config=model.custom_config,
        docker_image_id=image.id,
        docker_image_name=image.image_name,
        ssh_key=ssh_key,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
    )


def launch_reviewer(
    conn: sqlite3.Connection, project_id: int, adapter: DockerAdapter
) -> ReviewerLaunchResult:
    """Launch one reviewer for a project. Raises if cap is reached or launch fails."""
    project = repos.project_get(conn, project_id)
    if project is None:
        raise ReviewerError(f"Project {project_id} not found")

    cap = _reviewer_cap(project)
    running = _running_reviewer_count(conn, project_id)
    if running >= cap:
        raise ReviewerError(
            f"Reviewer cap ({cap}) reached for project '{project.name}'"
        )

    spec = build_launch_spec(conn, project)
    try:
        result = launch_agent(conn, spec, adapter)
    except LaunchError as exc:
        raise ReviewerError(str(exc)) from exc

    instance_id = int(result.labels[LABEL_INSTANCE])

    repos.audit_event_create(
        conn,
        "reviewer_launch",
        project_id=project_id,
        agent_instance_id=instance_id,
        payload={"container_id": result.container_id, "container_name": result.container_name},
    )
    return ReviewerLaunchResult(
        agent_instance_id=instance_id,
        container_id=result.container_id,
        container_name=result.container_name,
    )


def trigger_reviewer(
    conn: sqlite3.Connection,
    project_id: int,
    trigger_service: TriggerService,
    *,
    manual: bool = True,
) -> TriggerOutcome:
    """Manually fire a trigger on the first running reviewer for a project."""
    project = repos.project_get(conn, project_id)
    if project is None:
        raise ReviewerError(f"Project {project_id} not found")

    instances = repos.agent_instance_list_for_project(conn, project_id)
    reviewer = next(
        (
            inst
            for inst in instances
            if inst.agent_type == "reviewer" and inst.status == "running"
        ),
        None,
    )
    if reviewer is None:
        raise ReviewerError(f"No running reviewer for project '{project.name}'")

    interval = int(
        project.reviewer_config.get(
            "trigger_interval_minutes", repos.DEFAULT_REVIEWER_INTERVAL_MINUTES
        )
    )
    req = trigger_request_for_instance(
        conn, reviewer.id, _DEFAULT_REVIEWER_PROMPT, interval
    )
    if req is None:
        raise ReviewerError(
            f"Cannot build trigger request for reviewer {reviewer.id}"
        )
    return trigger_service.run(conn, req, manual=manual)


def terminate_reviewer(
    conn: sqlite3.Connection, agent_instance_id: int, adapter: DockerAdapter
) -> None:
    """Stop and remove a reviewer container, clean up credentials, mark terminated."""
    instance = repos.agent_instance_get(conn, agent_instance_id)
    if instance is None:
        raise ReviewerError(f"Agent instance {agent_instance_id} not found")
    if instance.agent_type != "reviewer":
        raise ReviewerError(f"Agent instance {agent_instance_id} is not a reviewer")

    if instance.container_id:
        adapter.stop(instance.container_id)
        repos.audit_event_create(
            conn,
            "container_stop",
            project_id=instance.project_id,
            agent_instance_id=agent_instance_id,
            payload={"container_id": instance.container_id},
        )
        adapter.remove(instance.container_id)
        repos.audit_event_create(
            conn,
            "container_remove",
            project_id=instance.project_id,
            agent_instance_id=agent_instance_id,
            payload={"container_id": instance.container_id},
        )

    # Clean up temp credential files persisted during launch.
    snapshot = repos.config_snapshot_get(conn, agent_instance_id)
    if snapshot and snapshot.get("credential_dir"):
        cleanup_credential_dir(Path(snapshot["credential_dir"]))

    repos.agent_instance_update(conn, agent_instance_id, status="terminated")
    repos.audit_event_create(
        conn,
        "reviewer_terminate",
        project_id=instance.project_id,
        agent_instance_id=agent_instance_id,
        payload={"container_id": instance.container_id},
    )


def get_reviewer_status(conn: sqlite3.Connection, project_id: int) -> dict[str, object]:
    """Expose reviewer count, cap, and running instances for project views."""
    project = repos.project_get(conn, project_id)
    if project is None:
        raise ReviewerError(f"Project {project_id} not found")

    cap = _reviewer_cap(project)
    instances = repos.agent_instance_list_for_project(conn, project_id)
    reviewers = [
        inst
        for inst in instances
        if inst.agent_type == "reviewer" and inst.status == "running"
    ]
    return {
        "project_id": project_id,
        "reviewer_cap": cap,
        "running_reviewers": len(reviewers),
        "reviewers": [
            {
                "agent_instance_id": inst.id,
                "container_id": inst.container_id,
                "container_name": inst.container_name,
                "session_id": inst.session_id,
                "status": inst.status,
            }
            for inst in reviewers
        ],
    }
