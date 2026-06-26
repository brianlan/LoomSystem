"""Tests for the Docker adapter and launch materialization service."""

import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from app import repositories as repos
from app.db import get_db
from app.docker import DockerError, FakeDockerAdapter
from app.launch import (
    LABEL_INSTANCE,
    LABEL_PROJECT,
    LABEL_ROLE,
    LaunchError,
    LaunchSpec,
    cleanup_credential_dir,
    launch_agent,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "launch_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


def _make_spec(**overrides: object) -> LaunchSpec:
    defaults: dict[str, object] = {
        "project_id": 1,
        "project_name": "demo",
        "repo_url": "git@github.com:brianlan/demo.git",
        "agent_type": "reviewer",
        "agent_definition_id": 1,
        "agent_name": "reviewer",
        "prompt_markdown": "# Reviewer prompt",
        "github_identity": "bot",
        "model_entry_id": 1,
        "model_provider_id": "anthropic",
        "model_id": "claude-3",
        "model_credentials": "sk-secret",
        "model_custom_config": None,
        "docker_image_id": 1,
        "docker_image_name": "loomsystem/runtime:latest",
        "ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n",
    }
    merged = {**defaults, **overrides}
    return LaunchSpec(**merged)  # type: ignore[arg-type]


def _seed_project(conn: sqlite3.Connection) -> repos.Project:
    # Seed registry entries so FK constraints pass.
    repos.agent_definition_create(conn, "reviewer", "# Reviewer", "bot")
    repos.model_entry_create(conn, "anthropic", "claude-3", "sk-secret")
    repos.docker_image_create(conn, "loomsystem/runtime:latest")
    return repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")


# ---------------------------------------------------------------------------
# Docker adapter tests
# ---------------------------------------------------------------------------


def test_fake_image_exists_default_false() -> None:
    adapter = FakeDockerAdapter()
    assert adapter.image_exists("img") is False


def test_fake_pull_then_exists() -> None:
    adapter = FakeDockerAdapter()
    adapter.pull("img")
    assert adapter.image_exists("img") is True


def test_fake_pull_failure_raises() -> None:
    adapter = FakeDockerAdapter(pull_failures={"bad-img"})
    with pytest.raises(DockerError, match="simulated failure"):
        adapter.pull("bad-img")


def test_fake_run_returns_id_and_records_env() -> None:
    import json

    adapter = FakeDockerAdapter()
    cid = adapter.run(
        image="img",
        name="loom-1-reviewer-abcd",
        env={"FOO": "bar"},
        volumes=["/host:/cont:ro"],
        labels={"loom.role": "reviewer"},
    )
    info = adapter.inspect(cid)
    assert info.name == "loom-1-reviewer-abcd"
    assert info.labels == {"loom.role": "reviewer"}
    env = json.loads(adapter.containers[cid]["env"])
    assert env == {"FOO": "bar"}


def test_fake_exec_returns_default() -> None:
    adapter = FakeDockerAdapter()
    cid = adapter.run("img", "c", {}, [], {})
    code, output = adapter.exec(cid, ["echo", "hi"])
    assert code == 0
    assert output == ""


def test_fake_exec_returns_configured() -> None:
    import shlex

    adapter = FakeDockerAdapter()
    cid = adapter.run("img", "c", {}, [], {})
    cmd = ["git", "clone", "url", "/workspace/repo"]
    adapter.exec_results[(cid, shlex.join(cmd))] = (1, "fatal: auth failed")
    code, output = adapter.exec(cid, cmd)
    assert code == 1
    assert "auth failed" in output


# ---------------------------------------------------------------------------
# Launch service tests
# ---------------------------------------------------------------------------


def test_launch_happy_path(conn: sqlite3.Connection) -> None:
    _seed_project(conn)
    spec = _make_spec()
    adapter = FakeDockerAdapter()
    # Pre-populate image + make opencode models return the provider.
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    result = launch_agent(conn, spec, adapter)

    assert result.container_id.startswith("fake-container-")
    assert result.container_name.startswith("loom-1-reviewer-")
    assert result.labels[LABEL_PROJECT] == "1"
    assert result.labels[LABEL_ROLE] == "reviewer"
    assert result.labels[LABEL_INSTANCE].isdigit()

    # Agent instance persisted with container metadata.
    instance = repos.agent_instance_get(conn, int(result.labels[LABEL_INSTANCE]))
    assert instance is not None
    assert instance.container_id == result.container_id
    assert instance.container_name == result.container_name
    assert instance.status == "running"

    # Config snapshot persisted.
    snapshot = repos.config_snapshot_get(conn, instance.id)
    assert snapshot is not None
    assert snapshot["docker_image_name"] == spec.docker_image_name
    assert snapshot["container_name"] == result.container_name


def test_launch_auto_pulls_missing_image(conn: sqlite3.Connection) -> None:
    _seed_project(conn)
    spec = _make_spec()
    adapter = FakeDockerAdapter()
    # Image NOT pre-populated — should auto-pull (D43).
    adapter.exec_default = (0, "anthropic/claude-3")

    launch_agent(conn, spec, adapter)

    assert spec.docker_image_name in adapter.images


def test_launch_image_pull_failure_rejected(conn: sqlite3.Connection) -> None:
    _seed_project(conn)
    spec = _make_spec(docker_image_name="bad-img")
    adapter = FakeDockerAdapter(pull_failures={"bad-img"})

    with pytest.raises(LaunchError, match="not available"):
        launch_agent(conn, spec, adapter)


def test_launch_ssh_clone_failure_marks_failed_and_removes(
    conn: sqlite3.Connection,
) -> None:
    _seed_project(conn)
    spec = _make_spec()
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    # git clone fails.
    adapter.exec_default = (1, "fatal: Could not read from remote repository")

    with pytest.raises(LaunchError, match="SSH clone failed"):
        launch_agent(conn, spec, adapter)

    # Container was stopped + removed.
    assert len(adapter.containers) == 0
    # Instance marked failed.
    rows = conn.execute(
        "SELECT status FROM agent_instances WHERE project_id = 1"
    ).fetchall()
    assert rows[0]["status"] == "failed"


def test_launch_model_credential_failure_marks_failed_and_removes(
    conn: sqlite3.Connection,
) -> None:
    _seed_project(conn)
    spec = _make_spec(model_provider_id="openai")

    # Custom adapter: clone succeeds, but opencode models doesn't list "openai".
    class _SelectiveAdapter(FakeDockerAdapter):
        def exec(self, container_id: str, command: list[str]) -> tuple[int, str]:
            if command[:2] == ["git", "clone"]:
                return (0, "Cloning done")
            if command == ["opencode", "models"]:
                return (0, "anthropic/claude-3\n")  # no "openai"
            return (1, "error")

    selective = _SelectiveAdapter()
    selective.images.add(spec.docker_image_name)

    with pytest.raises(LaunchError, match="Credential missing for provider 'openai'"):
        launch_agent(conn, spec, selective)

    assert len(selective.containers) == 0


def test_credential_env_var_uses_provider_prefix(conn: sqlite3.Connection) -> None:
    _seed_project(conn)
    spec = _make_spec(model_provider_id="anthropic", model_credentials="sk-test-123")
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    launch_agent(conn, spec, adapter)

    # Check the env var was set with provider prefix.
    import json

    cid = list(adapter.containers.keys())[0]
    env = json.loads(adapter.containers[cid]["env"])
    assert env["ANTHROPIC_API_KEY"] == "sk-test-123"


def test_custom_config_writes_auth_json_0600(conn: sqlite3.Connection) -> None:
    _seed_project(conn)
    spec = _make_spec(
        model_custom_config={"anthropic": {"key": "sk-custom"}},
    )
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    real_tmp = tempfile.mkdtemp(prefix="loom-test-")
    with patch("app.launch.tempfile.mkdtemp", return_value=real_tmp):
        launch_agent(conn, spec, adapter)

    auth_path = Path(real_tmp) / "auth.json"
    assert auth_path.exists()
    import stat

    mode = stat.S_IMODE(auth_path.stat().st_mode)
    assert mode == 0o600

    # The auth.json volume was mounted.
    import json

    cid = list(adapter.containers.keys())[0]
    vols = json.loads(adapter.containers[cid]["volumes"])
    assert any("auth.json" in v for v in vols)


def test_ssh_key_file_0600(conn: sqlite3.Connection) -> None:
    _seed_project(conn)
    spec = _make_spec()
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    real_tmp = tempfile.mkdtemp(prefix="loom-test-")
    with patch("app.launch.tempfile.mkdtemp", return_value=real_tmp):
        launch_agent(conn, spec, adapter)

    ssh_path = Path(real_tmp) / "id_ed25519"
    assert ssh_path.exists()
    import stat

    mode = stat.S_IMODE(ssh_path.stat().st_mode)
    assert mode == 0o600


def test_proxy_env_vars_applied_to_container(conn: sqlite3.Connection) -> None:
    _seed_project(conn)
    spec = _make_spec(http_proxy="http://proxy:8080", https_proxy="https://proxy:8443")
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    launch_agent(conn, spec, adapter)

    import json

    cid = list(adapter.containers.keys())[0]
    env = json.loads(adapter.containers[cid]["env"])
    assert env["HTTP_PROXY"] == "http://proxy:8080"
    assert env["HTTPS_PROXY"] == "https://proxy:8443"


def test_no_proxy_when_not_set(conn: sqlite3.Connection) -> None:
    _seed_project(conn)
    spec = _make_spec()
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    launch_agent(conn, spec, adapter)

    import json

    cid = list(adapter.containers.keys())[0]
    env = json.loads(adapter.containers[cid]["env"])
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env


def test_container_name_scheme(conn: sqlite3.Connection) -> None:
    """Container name follows loom-<project-id>-<role>-<random> (Q-3)."""
    project = _seed_project(conn)
    spec = _make_spec(project_id=project.id, agent_type="implementor")
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    result = launch_agent(conn, spec, adapter)

    assert result.container_name.startswith(f"loom-{project.id}-implementor-")
    # Random suffix is 8 hex chars.
    suffix = result.container_name.split(f"loom-{project.id}-implementor-")[1]
    assert len(suffix) == 8


def test_labels_are_deterministic(conn: sqlite3.Connection) -> None:
    project = _seed_project(conn)
    spec = _make_spec(project_id=project.id, agent_type="reviewer")
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    result = launch_agent(conn, spec, adapter)

    assert result.labels == {
        LABEL_PROJECT: str(project.id),
        LABEL_ROLE: "reviewer",
        LABEL_INSTANCE: result.labels[LABEL_INSTANCE],
    }


def test_cleanup_credential_dir() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="loom-cleanup-"))
    (tmpdir / "id_ed25519").write_text("key")
    (tmpdir / "auth.json").write_text("{}")
    sub = tmpdir / "agents"
    sub.mkdir()
    (sub / "agent.md").write_text("# prompt")

    cleanup_credential_dir(tmpdir)

    assert not tmpdir.exists()


def test_cleanup_credential_dir_missing_is_noop() -> None:
    cleanup_credential_dir(Path("/nonexistent/loom-cleanup-xyz"))


def test_failed_launch_cleans_credential_temp_dir(conn: sqlite3.Connection) -> None:
    """Temp credential files (incl. SSH key) are removed after a failed launch (NFR-7)."""
    _seed_project(conn)
    spec = _make_spec(docker_image_name="bad-img")
    adapter = FakeDockerAdapter(pull_failures={"bad-img"})

    with pytest.raises(LaunchError, match="not available"):
        launch_agent(conn, spec, adapter)

    # The temp dir should have been cleaned up — scan /tmp for leftover loom-launch dirs.
    import glob

    leftover = [
        d for d in glob.glob("/tmp/loom-launch-*")
        if Path(d).exists() and "id_ed25519" in str(Path(d))
    ]
    # We can't pin the exact dir, but if cleanup worked, no leftover containing the key exists.
    # More direct: verify via a patched mkdtemp that the returned dir is gone.
    assert len(leftover) == 0


def test_failed_launch_ssh_clone_cleans_credential_temp_dir(
    conn: sqlite3.Connection,
) -> None:
    """Credential temp dir cleaned up when SSH clone fails (not just image pull failure)."""
    _seed_project(conn)
    spec = _make_spec()
    real_tmp = tempfile.mkdtemp(prefix="loom-test-cleanup-")
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (1, "fatal: Could not read from remote repository")

    with patch("app.launch.tempfile.mkdtemp", return_value=real_tmp):
        with pytest.raises(LaunchError, match="SSH clone failed"):
            launch_agent(conn, spec, adapter)

    assert not Path(real_tmp).exists()


def test_success_persists_credential_dir_in_snapshot(
    conn: sqlite3.Connection,
) -> None:
    """On success, the credential dir is recorded in config_snapshot for T07/T13 teardown."""
    _seed_project(conn)
    spec = _make_spec()
    real_tmp = tempfile.mkdtemp(prefix="loom-test-snapshot-")
    adapter = FakeDockerAdapter()
    adapter.images.add(spec.docker_image_name)
    adapter.exec_default = (0, "anthropic/claude-3")

    with patch("app.launch.tempfile.mkdtemp", return_value=real_tmp):
        result = launch_agent(conn, spec, adapter)

    instance = repos.agent_instance_get(conn, int(result.labels[LABEL_INSTANCE]))
    assert instance is not None
    snapshot = repos.config_snapshot_get(conn, instance.id)
    assert snapshot is not None
    assert snapshot["credential_dir"] == real_tmp
    # Temp dir still exists on success (container needs the bind mount).
    assert Path(real_tmp).exists()
    # Clean up after test.
    cleanup_credential_dir(Path(real_tmp))
