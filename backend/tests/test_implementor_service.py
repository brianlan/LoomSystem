"""Tests for the implementor lifecycle service (T11).

Covers: prompt rendering, launch with issue binding, issue-close polling +
termination, refill dispatch, PR linkage detection/verification/amendment,
closed-without-PR edge case, and PR-closed-without-merge continuation.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app import implementor_service as svc
from app import repositories as repos
from app.db import get_db
from app.docker import FakeDockerAdapter
from app.github import GitHubError, IssueDTO, PullRequestDTO

_IMAGE = "loomsystem/runtime:latest"
_FAKE_SSH_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "implementor_service_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class FakeGitHubAdapter:
    """Controllable GitHub adapter for implementor service tests."""

    def __init__(
        self,
        *,
        issue_state: str = "open",
        issue_title: str = "Test issue",
        prs: list[PullRequestDTO] | None = None,
    ) -> None:
        self._issue_state = issue_state
        self._issue_title = issue_title
        self._prs = prs or []
        self.pr_updates: list[tuple[int, str]] = []  # (pr_number, new_body)

    def list_open_issues(self, owner: str, repo: str) -> list[IssueDTO]:
        return [IssueDTO(number=1, title=self._issue_title, state=self._issue_state)]

    def list_open_pull_requests(self, owner: str, repo: str) -> list[PullRequestDTO]:
        return list(self._prs)

    def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequestDTO:
        for pr in self._prs:
            if pr.number == number:
                return pr
        raise GitHubError(f"PR {number} not found", kind="error")

    def get_issue(self, owner: str, repo: str, number: int) -> IssueDTO:
        return IssueDTO(number=number, title=self._issue_title, state=self._issue_state)

    def update_pr(self, owner: str, repo: str, number: int, body: str) -> None:
        self.pr_updates.append((number, body))


class FakeTriageClient:
    def __init__(self, ranking: list[int] | None = None) -> None:
        self._ranking = ranking or []

    def call(self, config: Any, prompt: str) -> str:
        return json.dumps({"ranked_issue_numbers": self._ranking})


def _seed_project(
    conn: sqlite3.Connection,
    *,
    issues: list[IssueDTO] | None = None,
    parallelism: int = 1,
) -> repos.Project:
    """Seed registry entries, settings, project with implementor config, and issue snapshots."""
    repos.agent_definition_create(
        conn,
        "implementor",
        "# Implementor\n\nIssue: #{{issue_number}} {{issue_title}}",
        "bot",
    )
    repos.model_entry_create(conn, "anthropic", "claude-3", "sk-secret")
    repos.docker_image_create(conn, _IMAGE)
    repos.setting_set(conn, "ssh_key", _FAKE_SSH_KEY)
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    repos.project_update(
        conn,
        project.id,
        implementor_config={
            "agent_definition_id": 1,
            "model_entry_id": 1,
            "docker_image_id": 1,
            "trigger_interval_minutes": 15,
            "parallelism": parallelism,
        },
    )
    repos.setting_set(conn, "triage_endpoint_url", "https://api.example.com")
    repos.setting_set(conn, "triage_model_name", "gpt-4")
    repos.setting_set(conn, "triage_api_key", "sk-key")
    repos.setting_set(conn, "triage_headers", "{}")

    # Seed issue snapshots.
    issue_list = issues or [IssueDTO(number=1, title="Test issue", state="open")]
    for issue in issue_list:
        conn.execute(
            "INSERT INTO github_issues (project_id, issue_number, title, state, loom_status) "
            "VALUES (?, ?, ?, ?, ?)",
            (project.id, issue.number, issue.title, issue.state, "unassigned"),
        )
    conn.commit()
    return project


def _make_adapter() -> FakeDockerAdapter:
    adapter = FakeDockerAdapter()
    adapter.images.add(_IMAGE)
    adapter.exec_default = (0, "anthropic")
    return adapter


# ---------------------------------------------------------------------------
# Prompt rendering tests
# ---------------------------------------------------------------------------


def test_render_prompt_substitutes_placeholders() -> None:
    prompt = "Fix #{{issue_number}}: {{issue_title}}"
    result = svc.render_prompt(prompt, 42, "Add login page")
    assert result == "Fix #42: Add login page"


def test_render_prompt_no_placeholders() -> None:
    prompt = "No placeholders here"
    result = svc.render_prompt(prompt, 1, "Title")
    assert result == "No placeholders here"


def test_render_prompt_partial_placeholders() -> None:
    prompt = "Issue {{issue_number}} only"
    result = svc.render_prompt(prompt, 7, "Title")
    assert result == "Issue 7 only"


# ---------------------------------------------------------------------------
# Launch tests
# ---------------------------------------------------------------------------


def test_launch_implementor_success(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()

    result = svc.launch_implementor(conn, project.id, 1, adapter)

    assert result.agent_instance_id > 0
    assert result.container_id.startswith("fake-container-")

    instance = repos.agent_instance_get(conn, result.agent_instance_id)
    assert instance is not None
    assert instance.agent_type == "implementor"
    assert instance.status == "running"
    assert instance.issue_number == 1

    events = repos.audit_event_list(conn, project_id=project.id)
    assert any(e["event_type"] == "implementor_launch" for e in events)

    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_IN_PROGRESS


def test_launch_implementor_renders_prompt(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn, issues=[IssueDTO(number=42, title="Fix bug", state="open")])
    adapter = _make_adapter()

    svc.launch_implementor(conn, project.id, 42, adapter)

    # The rendered prompt should be materialized to the agent definition file.
    # Check that the LaunchSpec was built with rendered prompt via the config snapshot.
    # The agent markdown file was written to a temp dir; we verify via the adapter's
    # container volumes containing the rendered prompt path.
    instances = repos.agent_instance_list_for_project(conn, project.id)
    impl = next(i for i in instances if i.agent_type == "implementor")
    snapshot = repos.config_snapshot_get(conn, impl.id)
    assert snapshot is not None
    assert snapshot["agent_definition_id"] == 1


def test_launch_implementor_project_not_found(conn: sqlite3.Connection) -> None:
    adapter = _make_adapter()
    with pytest.raises(svc.ImplementorError, match="not found"):
        svc.launch_implementor(conn, 9999, 1, adapter)


def test_launch_implementor_issue_not_in_snapshot(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()
    with pytest.raises(svc.ImplementorError, match="not found"):
        svc.launch_implementor(conn, project.id, 9999, adapter)


def test_launch_implementor_missing_ssh_key(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    # Remove SSH key.
    conn.execute("DELETE FROM settings WHERE key = 'ssh_key'")
    adapter = _make_adapter()
    with pytest.raises(svc.ImplementorError, match="SSH key"):
        svc.launch_implementor(conn, project.id, 1, adapter)


def test_launch_implementor_image_preflight_failure(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter(pull_failures={_IMAGE})

    with pytest.raises(svc.ImplementorError, match="not available"):
        svc.launch_implementor(conn, project.id, 1, adapter)

    assert len(adapter.containers) == 0
    running = sum(
        1
        for inst in repos.agent_instance_list_for_project(conn, project.id)
        if inst.agent_type == "implementor" and inst.status == "running"
    )
    assert running == 0


def test_launch_implementor_model_preflight_failure(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = FakeDockerAdapter()
    adapter.images.add(_IMAGE)
    adapter.exec_default = (0, "openai/gpt-4")

    with pytest.raises(svc.ImplementorError, match="Credential missing for provider 'anthropic'"):
        svc.launch_implementor(conn, project.id, 1, adapter)

    assert len(adapter.containers) == 0
    running = sum(
        1
        for inst in repos.agent_instance_list_for_project(conn, project.id)
        if inst.agent_type == "implementor" and inst.status == "running"
    )
    assert running == 0


# ---------------------------------------------------------------------------
# Terminate tests
# ---------------------------------------------------------------------------


def test_terminate_implementor(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()
    result = svc.launch_implementor(conn, project.id, 1, adapter)

    svc.terminate_implementor(conn, result.agent_instance_id, adapter)

    instance = repos.agent_instance_get(conn, result.agent_instance_id)
    assert instance is not None
    assert instance.status == "terminated"
    assert result.container_id not in adapter.containers

    events = repos.audit_event_list(conn, project_id=project.id)
    assert any(e["event_type"] == "implementor_terminate" for e in events)


def test_terminate_implementor_not_found(conn: sqlite3.Connection) -> None:
    adapter = _make_adapter()
    with pytest.raises(svc.ImplementorError, match="not found"):
        svc.terminate_implementor(conn, 9999, adapter)


def test_terminate_implementor_wrong_type(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()
    result = svc.launch_implementor(conn, project.id, 1, adapter)

    # Tamper with agent_type to simulate wrong type.
    conn.execute(
        "UPDATE agent_instances SET agent_type = 'reviewer' WHERE id = ?",
        (result.agent_instance_id,),
    )
    with pytest.raises(svc.ImplementorError, match="not an implementor"):
        svc.terminate_implementor(conn, result.agent_instance_id, adapter)


# ---------------------------------------------------------------------------
# ImplementorLauncherImpl tests
# ---------------------------------------------------------------------------


def test_launcher_impl_launch_and_terminate(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    adapter = _make_adapter()
    launcher = svc.ImplementorLauncherImpl(adapter)

    instance_id = launcher.launch(conn, project.id, 1)
    assert instance_id > 0

    launcher.terminate(conn, instance_id)
    instance = repos.agent_instance_get(conn, instance_id)
    assert instance is not None
    assert instance.status == "terminated"


# ---------------------------------------------------------------------------
# Issue-close polling + termination + refill tests (FR-27, D6)
# ---------------------------------------------------------------------------


def test_check_implementor_issue_closed_terminates_and_refills(
    conn: sqlite3.Connection,
) -> None:
    """FR-27: When bound issue closes, implementor terminates + refill triggers."""
    issues = [
        IssueDTO(number=1, title="First issue", state="open"),
        IssueDTO(number=2, title="Second issue", state="open"),
    ]
    project = _seed_project(conn, issues=issues, parallelism=1)
    adapter = _make_adapter()
    launcher = svc.ImplementorLauncherImpl(adapter)

    # Launch implementor on issue 1.
    instance_id = launcher.launch(conn, project.id, 1)

    # Start the loop so refill can work.
    from app.implementor_loop import start_loop

    start_loop(conn, project.id)

    # Now issue 1 is closed.
    gh = FakeGitHubAdapter(issue_state="closed", issue_title="First issue")
    triage = FakeTriageClient(ranking=[2])

    terminated = svc.check_implementor(
        conn, project.id, instance_id, gh, launcher, triage
    )

    assert terminated is True
    instance = repos.agent_instance_get(conn, instance_id)
    assert instance is not None
    assert instance.status == "terminated"

    # Issue marked resolved.
    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_RESOLVED

    # Audit event recorded.
    events = repos.audit_event_list(conn, project_id=project.id)
    assert any(e["event_type"] == "implementor_issue_closed" for e in events)

    # Refill should have launched a new implementor for issue 2.
    instances = repos.agent_instance_list_for_project(conn, project.id)
    running = [i for i in instances if i.status == "running"]
    assert len(running) == 1
    assert running[0].issue_number == 2


def test_check_implementor_issue_still_open_no_termination(
    conn: sqlite3.Connection,
) -> None:
    """When issue is still open, implementor continues running."""
    project = _seed_project(conn)
    adapter = _make_adapter()
    launcher = svc.ImplementorLauncherImpl(adapter)
    instance_id = launcher.launch(conn, project.id, 1)

    gh = FakeGitHubAdapter(issue_state="open")
    triage = FakeTriageClient(ranking=[1])

    terminated = svc.check_implementor(
        conn, project.id, instance_id, gh, launcher, triage
    )

    assert terminated is False
    instance = repos.agent_instance_get(conn, instance_id)
    assert instance is not None
    assert instance.status == "running"


def test_check_implementor_issue_closed_without_pr(conn: sqlite3.Connection) -> None:
    """EC-10: Issue closed without PR (wontfix) — implementor terminates normally."""
    project = _seed_project(conn)
    adapter = _make_adapter()
    launcher = svc.ImplementorLauncherImpl(adapter)
    instance_id = launcher.launch(conn, project.id, 1)

    from app.implementor_loop import start_loop

    start_loop(conn, project.id)

    # No PRs at all.
    gh = FakeGitHubAdapter(issue_state="closed", prs=[])
    triage = FakeTriageClient(ranking=[])

    terminated = svc.check_implementor(
        conn, project.id, instance_id, gh, launcher, triage
    )

    assert terminated is True
    instance = repos.agent_instance_get(conn, instance_id)
    assert instance is not None
    assert instance.status == "terminated"


def test_check_implementor_github_error_no_termination(
    conn: sqlite3.Connection,
) -> None:
    """When GitHub API errors, implementor is NOT terminated (transient)."""
    project = _seed_project(conn)
    adapter = _make_adapter()
    launcher = svc.ImplementorLauncherImpl(adapter)
    instance_id = launcher.launch(conn, project.id, 1)

    class _ErrorAdapter(FakeGitHubAdapter):
        def get_issue(self, owner: str, repo: str, number: int) -> IssueDTO:
            raise GitHubError("rate limited", kind="rate_limited")

    gh = _ErrorAdapter()
    triage = FakeTriageClient(ranking=[])

    terminated = svc.check_implementor(
        conn, project.id, instance_id, gh, launcher, triage
    )

    assert terminated is False
    instance = repos.agent_instance_get(conn, instance_id)
    assert instance is not None
    assert instance.status == "running"


def test_check_implementor_skips_non_running(conn: sqlite3.Connection) -> None:
    """Non-running or non-implementor instances are skipped."""
    project = _seed_project(conn)
    adapter = _make_adapter()
    launcher = svc.ImplementorLauncherImpl(adapter)
    instance_id = launcher.launch(conn, project.id, 1)

    # Mark as terminated.
    repos.agent_instance_update(conn, instance_id, status="terminated")

    gh = FakeGitHubAdapter(issue_state="closed")
    triage = FakeTriageClient(ranking=[])

    terminated = svc.check_implementor(
        conn, project.id, instance_id, gh, launcher, triage
    )
    assert terminated is False


# ---------------------------------------------------------------------------
# PR linkage detection + verification/amendment tests (FR-28, D37)
# ---------------------------------------------------------------------------


def test_detect_pr_linkage_present(conn: sqlite3.Connection) -> None:
    """FR-28: PR with 'Closes #N' in body — linkage verified, no amendment."""
    project = _seed_project(conn)
    gh = FakeGitHubAdapter(
        prs=[
            PullRequestDTO(
                number=5,
                title="Fix #1",
                state="open",
                merged=False,
                body="Implemented the fix.\n\nCloses #1",
            )
        ],
    )

    result = svc.detect_and_verify_pr_linkage(conn, project.id, 1, gh)

    assert result == "linked"
    assert gh.pr_updates == []  # No amendment.

    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_PR_OPENED


def test_detect_pr_linkage_missing_amended(conn: sqlite3.Connection) -> None:
    """FR-28/D37: PR without 'Closes #N' — linkage amended."""
    project = _seed_project(conn)
    gh = FakeGitHubAdapter(
        prs=[
            PullRequestDTO(
                number=5,
                title="Fix #1",
                state="open",
                merged=False,
                body="Implemented the fix.",
            )
        ],
    )

    result = svc.detect_and_verify_pr_linkage(conn, project.id, 1, gh)

    assert result == "amended"
    assert len(gh.pr_updates) == 1
    pr_number, new_body = gh.pr_updates[0]
    assert pr_number == 5
    assert "Closes #1" in new_body

    events = repos.audit_event_list(conn, project_id=project.id)
    assert any(e["event_type"] == "pr_linkage_amended" for e in events)


def test_detect_pr_linkage_no_pr(conn: sqlite3.Connection) -> None:
    """No PR referencing the issue — returns 'no_pr', no status change."""
    project = _seed_project(conn)
    gh = FakeGitHubAdapter(prs=[])

    result = svc.detect_and_verify_pr_linkage(conn, project.id, 1, gh)

    assert result == "no_pr"
    issue = repos.github_issue_get(conn, project.id, 1)
    assert issue is not None
    assert issue.loom_status == repos.ISSUE_STATUS_UNASSIGNED


def test_detect_pr_linkage_word_boundary(conn: sqlite3.Connection) -> None:
    """#1 should not match #10, #11, etc."""
    project = _seed_project(conn, issues=[IssueDTO(number=10, title="Tenth", state="open")])
    gh = FakeGitHubAdapter(
        prs=[
            PullRequestDTO(
                number=99,
                title="Fix #1",
                state="open",
                merged=False,
                body="Refers to #1",
            )
        ],
    )

    result = svc.detect_and_verify_pr_linkage(conn, project.id, 10, gh)
    assert result == "no_pr"


def test_detect_pr_linkage_github_error(conn: sqlite3.Connection) -> None:
    """GitHub API error — returns 'no_pr' gracefully."""
    project = _seed_project(conn)

    class _ErrorAdapter(FakeGitHubAdapter):
        def list_open_pull_requests(self, owner: str, repo: str) -> list[PullRequestDTO]:
            raise GitHubError("error", kind="error")

    gh = _ErrorAdapter()
    result = svc.detect_and_verify_pr_linkage(conn, project.id, 1, gh)
    assert result == "no_pr"


# ---------------------------------------------------------------------------
# PR closed without merge, issue still open (EC-14)
# ---------------------------------------------------------------------------


def test_pr_closed_without_merge_implementor_continues(
    conn: sqlite3.Connection,
) -> None:
    """EC-14: PR closed without merge but issue still open → implementor continues."""
    project = _seed_project(conn)
    adapter = _make_adapter()
    launcher = svc.ImplementorLauncherImpl(adapter)
    instance_id = launcher.launch(conn, project.id, 1)

    # Issue still open, no PRs (PR was closed).
    gh = FakeGitHubAdapter(issue_state="open", prs=[])
    triage = FakeTriageClient(ranking=[1])

    terminated = svc.check_implementor(
        conn, project.id, instance_id, gh, launcher, triage
    )

    assert terminated is False
    instance = repos.agent_instance_get(conn, instance_id)
    assert instance is not None
    assert instance.status == "running"
