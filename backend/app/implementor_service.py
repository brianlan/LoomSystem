"""Implementor lifecycle service: issue-bound launch, issue-close polling,
termination, PR linkage verification/amendment, and refill dispatch (T11).

Thin domain layer over the shared launch (T06) and implementor-loop (T10)
services. Each implementor is 1:1 bound to a GitHub issue; it terminates when
the issue closes for any reason (D6), and the system verifies that any PR it
opens includes ``Closes #<issue-number>`` (D37/FR-28).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import repositories as repos
from app.docker import DockerAdapter
from app.github import GitHubAdapter, GitHubError, PullRequestDTO, parse_repo_url
from app.implementor_loop import ImplementorLauncher, RefillResult, refill
from app.launch import (
    LABEL_INSTANCE,
    LaunchError,
    LaunchSpec,
    cleanup_credential_dir,
    launch_agent,
)
from app.triage import TriageClient


class ImplementorError(Exception):
    """Raised when an implementor lifecycle operation fails."""


@dataclass(frozen=True)
class ImplementorLaunchResult:
    agent_instance_id: int
    container_id: str
    container_name: str


# ---------------------------------------------------------------------------
# Prompt rendering (FR-25, D24)
# ---------------------------------------------------------------------------


def render_prompt(prompt_markdown: str, issue_number: int, issue_title: str) -> str:
    """Replace {{issue_number}} and {{issue_title}} placeholders (FR-25, D24)."""
    return prompt_markdown.replace("{{issue_number}}", str(issue_number)).replace(
        "{{issue_title}}", issue_title
    )


# ---------------------------------------------------------------------------
# Launch (FR-25)
# ---------------------------------------------------------------------------


def build_launch_spec(
    conn: sqlite3.Connection,
    project: repos.Project,
    issue_number: int,
    issue_title: str,
) -> LaunchSpec:
    """Build a LaunchSpec from implementor_config + global settings, with rendered prompt."""
    config = project.implementor_config
    agent_def = repos.agent_definition_get(conn, config["agent_definition_id"])
    model = repos.model_entry_get(conn, config["model_entry_id"])
    image = repos.docker_image_get(conn, config["docker_image_id"])
    ssh_key = repos.setting_get(conn, "ssh_key")

    if agent_def is None:
        raise ImplementorError("Implementor agent definition not found")
    if model is None:
        raise ImplementorError("Implementor model entry not found")
    if image is None:
        raise ImplementorError("Implementor docker image not found")
    if not ssh_key:
        raise ImplementorError("SSH key not configured")

    http_proxy = repos.setting_get(conn, "http_proxy") or None
    https_proxy = repos.setting_get(conn, "https_proxy") or None

    return LaunchSpec(
        project_id=project.id,
        project_name=project.name,
        repo_url=project.repo_url,
        agent_type="implementor",
        agent_definition_id=agent_def.id,
        agent_name=agent_def.name,
        prompt_markdown=render_prompt(agent_def.prompt_markdown, issue_number, issue_title),
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


def launch_implementor(
    conn: sqlite3.Connection, project_id: int, issue_number: int, adapter: DockerAdapter
) -> ImplementorLaunchResult:
    """Launch one implementor bound to a specific issue (FR-25)."""
    project = repos.project_get(conn, project_id)
    if project is None:
        raise ImplementorError(f"Project {project_id} not found")

    issue = repos.github_issue_get(conn, project_id, issue_number)
    if issue is None:
        raise ImplementorError(f"Issue {issue_number} not found in project {project_id}")

    spec = build_launch_spec(conn, project, issue_number, issue.title)
    try:
        result = launch_agent(conn, spec, adapter)
    except LaunchError as exc:
        raise ImplementorError(str(exc)) from exc

    instance_id = int(result.labels[LABEL_INSTANCE])
    repos.agent_instance_update(conn, instance_id, issue_number=issue_number)
    repos.github_issue_set_status(
        conn, project_id, issue_number, repos.ISSUE_STATUS_IN_PROGRESS
    )
    repos.audit_event_create(
        conn,
        "implementor_launch",
        project_id=project_id,
        agent_instance_id=instance_id,
        payload={
            "container_id": result.container_id,
            "container_name": result.container_name,
            "issue_number": issue_number,
        },
    )
    return ImplementorLaunchResult(
        agent_instance_id=instance_id,
        container_id=result.container_id,
        container_name=result.container_name,
    )


def terminate_implementor(
    conn: sqlite3.Connection, agent_instance_id: int, adapter: DockerAdapter
) -> None:
    """Stop and remove an implementor container, clean up credentials, mark terminated."""
    instance = repos.agent_instance_get(conn, agent_instance_id)
    if instance is None:
        raise ImplementorError(f"Agent instance {agent_instance_id} not found")
    if instance.agent_type != "implementor":
        raise ImplementorError(f"Agent instance {agent_instance_id} is not an implementor")

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

    snapshot = repos.config_snapshot_get(conn, agent_instance_id)
    if snapshot and snapshot.get("credential_dir"):
        cleanup_credential_dir(Path(snapshot["credential_dir"]))

    repos.agent_instance_update(conn, agent_instance_id, status="terminated")
    repos.audit_event_create(
        conn,
        "implementor_terminate",
        project_id=instance.project_id,
        agent_instance_id=agent_instance_id,
        payload={"container_id": instance.container_id},
    )


# ---------------------------------------------------------------------------
# ImplementorLauncher implementation (wires T11 into T10's loop)
# ---------------------------------------------------------------------------


class ImplementorLauncherImpl:
    """Implements ImplementorLauncher Protocol using the shared Docker launch service."""

    def __init__(self, docker_adapter: DockerAdapter) -> None:
        self._docker_adapter = docker_adapter

    def launch(self, conn: sqlite3.Connection, project_id: int, issue_number: int) -> int:
        result = launch_implementor(conn, project_id, issue_number, self._docker_adapter)
        return result.agent_instance_id

    def terminate(self, conn: sqlite3.Connection, agent_instance_id: int) -> None:
        terminate_implementor(conn, agent_instance_id, self._docker_adapter)


# ---------------------------------------------------------------------------
# Issue-close polling + termination + refill (FR-27, D6)
# ---------------------------------------------------------------------------

# ponytail: 15-second poll cadence is a calling convention, not enforced here.
# The background loop that calls check_implementor chooses the interval.


def check_implementor(
    conn: sqlite3.Connection,
    project_id: int,
    agent_instance_id: int,
    github_adapter: GitHubAdapter,
    launcher: ImplementorLauncher,
    triage_client: TriageClient,
) -> bool:
    """FR-27: Poll one implementor's bound issue. Terminate + refill if closed.

    Returns True when the implementor was terminated. Designed to be called at
    ~15-second intervals (FR-27). When the issue is still open, also checks for
    PR linkage (FR-28).
    """
    instance = repos.agent_instance_get(conn, agent_instance_id)
    if (
        instance is None
        or instance.agent_type != "implementor"
        or instance.status != "running"
        or instance.issue_number is None
    ):
        return False

    project = repos.project_get(conn, project_id)
    if project is None:
        return False

    owner, repo = parse_repo_url(project.repo_url)
    issue_number = instance.issue_number

    try:
        issue = github_adapter.get_issue(owner, repo, issue_number)
    except GitHubError:
        return False  # Transient — next poll cycle retries.

    if issue.state == "closed":
        _terminate_and_refill(
            conn, project_id, agent_instance_id, issue_number, launcher, triage_client
        )
        return True

    # Issue still open — check PR linkage (FR-28).
    detect_and_verify_pr_linkage(conn, project_id, issue_number, github_adapter)
    return False


def _terminate_and_refill(
    conn: sqlite3.Connection,
    project_id: int,
    agent_instance_id: int,
    issue_number: int,
    launcher: ImplementorLauncher,
    triage_client: TriageClient,
) -> RefillResult:
    """Terminate the implementor, mark issue resolved, trigger refill (FR-27/D6)."""
    launcher.terminate(conn, agent_instance_id)
    repos.github_issue_set_status(
        conn, project_id, issue_number, repos.ISSUE_STATUS_RESOLVED
    )
    repos.audit_event_create(
        conn,
        "implementor_issue_closed",
        project_id=project_id,
        agent_instance_id=agent_instance_id,
        payload={"issue_number": issue_number},
    )
    return refill(conn, project_id, triage_client, launcher)


# ---------------------------------------------------------------------------
# PR linkage detection + verification/amendment (FR-28, D37)
# ---------------------------------------------------------------------------


# Word boundary so #10 doesn't match #100.
_PR_REF_RE_CACHE: dict[int, re.Pattern[str]] = {}


def _issue_ref_pattern(issue_number: int) -> re.Pattern[str]:
    pat = _PR_REF_RE_CACHE.get(issue_number)
    if pat is None:
        pat = re.compile(rf"#{issue_number}\b")
        _PR_REF_RE_CACHE[issue_number] = pat
    return pat


def _find_pr_for_issue(
    prs: list[PullRequestDTO], issue_number: int
) -> PullRequestDTO | None:
    """Find an open PR that references the given issue number in title or body."""
    pat = _issue_ref_pattern(issue_number)
    for pr in prs:
        text = f"{pr.title} {pr.body or ''}"
        if pat.search(text):
            return pr
    return None


def detect_and_verify_pr_linkage(
    conn: sqlite3.Connection,
    project_id: int,
    issue_number: int,
    github_adapter: GitHubAdapter,
) -> str:
    """FR-28/D37: Detect PR for issue, verify Closes #N, amend if missing.

    Returns ``'no_pr'``, ``'linked'``, or ``'amended'``.
    """
    project = repos.project_get(conn, project_id)
    if project is None:
        return "no_pr"

    owner, repo = parse_repo_url(project.repo_url)

    try:
        prs = github_adapter.list_open_pull_requests(owner, repo)
    except GitHubError:
        return "no_pr"

    pr = _find_pr_for_issue(prs, issue_number)
    if pr is None:
        return "no_pr"

    # PR found — transition issue to PR-opened.
    repos.github_issue_set_status(
        conn, project_id, issue_number, repos.ISSUE_STATUS_PR_OPENED
    )
    repos.audit_event_create(
        conn,
        "implementor_pr_opened",
        project_id=project_id,
        payload={"issue_number": issue_number, "pr_number": pr.number},
    )

    marker = f"Closes #{issue_number}"
    if marker in (pr.body or "") or marker in pr.title:
        return "linked"

    # Amend PR description (D37).
    new_body = f"{pr.body or ''}".rstrip() + f"\n\n{marker}"
    github_adapter.update_pr(owner, repo, pr.number, new_body)
    repos.audit_event_create(
        conn,
        "pr_linkage_amended",
        project_id=project_id,
        payload={"issue_number": issue_number, "pr_number": pr.number},
    )
    return "amended"
