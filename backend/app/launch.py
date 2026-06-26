"""Launch materialization service.

Prepares a Docker container for a reviewer or implementor agent:
1. Auto-pull the image if missing (D43, EC-7).
2. Create+start the container with credential env vars, proxy env vars, and
   mounted credential files (SSH key 0600, agent markdown, auth.json/opencode.json).
3. Clone the repo via SSH inside the container (D4, EC-8).
4. Validate model credential reachability (EC-9).
5. Persist container ID/name, labels, and a launch config snapshot.

opencode trigger execution (the first `opencode run`) is out of scope (T07).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app import repositories as repos
from app.docker import DockerAdapter, DockerError

# Label scheme — deterministic so restart recovery (T13) can find containers.
LABEL_PREFIX = "loom"
LABEL_PROJECT = f"{LABEL_PREFIX}.project-id"
LABEL_ROLE = f"{LABEL_PREFIX}.role"
LABEL_INSTANCE = f"{LABEL_PREFIX}.agent-instance-id"


class LaunchError(Exception):
    """Raised when launch preflight or materialization fails."""


@dataclass(frozen=True)
class LaunchSpec:
    """Everything needed to launch one agent container."""

    project_id: int
    project_name: str
    repo_url: str
    agent_type: str  # "reviewer" | "implementor"
    agent_definition_id: int
    agent_name: str
    prompt_markdown: str
    github_identity: str
    model_entry_id: int
    model_provider_id: str
    model_id: str
    model_credentials: str
    model_custom_config: dict[str, str] | None
    docker_image_id: int
    docker_image_name: str
    ssh_key: str
    http_proxy: str | None = None
    https_proxy: str | None = None


@dataclass(frozen=True)
class LaunchResult:
    container_id: str
    container_name: str
    labels: dict[str, str]


def _container_name(project_id: int, role: str) -> str:
    return f"{LABEL_PREFIX}-{project_id}-{role}-{secrets.token_hex(4)}"


def _labels(project_id: int, role: str, instance_id: int) -> dict[str, str]:
    return {
        LABEL_PROJECT: str(project_id),
        LABEL_ROLE: role,
        LABEL_INSTANCE: str(instance_id),
    }


def _materialize_credential_files(
    spec: LaunchSpec,
) -> tuple[dict[str, str], list[str], list[Path]]:
    """Write SSH key, agent markdown, and auth/opencode config to temp files.

    Returns (env_vars, docker_volumes, temp_paths_to_clean).
    Credential files are written with mode 0600 (NFR-7).
    """
    env: dict[str, str] = {}
    volumes: list[str] = []
    temp_paths: list[Path] = []

    tmpdir = Path(tempfile.mkdtemp(prefix="loom-launch-"))

    # SSH key — mounted read-only at /root/.ssh/id_ed25519 inside the container (D4).
    ssh_path = tmpdir / "id_ed25519"
    ssh_path.write_text(spec.ssh_key)
    os.chmod(ssh_path, 0o600)
    volumes.append(f"{ssh_path}:/root/.ssh/id_ed25519:ro")
    temp_paths.append(ssh_path)

    # Agent markdown — written to .opencode/agents/<name>.md inside the repo (D2, FR-16c).
    agent_dir = tmpdir / "agents"
    agent_dir.mkdir()
    agent_file = agent_dir / f"{spec.agent_name}.md"
    agent_file.write_text(spec.prompt_markdown)
    os.chmod(agent_file, 0o644)
    volumes.append(f"{agent_dir}:/workspace/.opencode/agents:ro")
    temp_paths.append(agent_dir)

    # Model credentials — env var for built-in providers (D35).
    # Conventional pattern: <PROVIDER>_API_KEY.
    env[f"{spec.model_provider_id.upper()}_API_KEY"] = spec.model_credentials

    # auth.json / opencode.json for custom/override providers (D35, NFR-7).
    if spec.model_custom_config:
        auth_path = tmpdir / "auth.json"
        auth_path.write_text(json.dumps(spec.model_custom_config))
        os.chmod(auth_path, 0o600)
        volumes.append(f"{auth_path}:/root/.config/opencode/auth.json:ro")
        temp_paths.append(auth_path)

    # Proxy env vars — container traffic only (FR-13, D15).
    if spec.http_proxy:
        env["HTTP_PROXY"] = spec.http_proxy
    if spec.https_proxy:
        env["HTTPS_PROXY"] = spec.https_proxy

    return env, volumes, temp_paths


def _validate_image(adapter: DockerAdapter, image: str) -> None:
    """Auto-pull missing images; reject on pull failure (D43, EC-7)."""
    if not adapter.image_exists(image):
        try:
            adapter.pull(image)
        except DockerError as exc:
            raise LaunchError(f"Image {image} not available: {exc}") from exc


def _clone_repo(
    adapter: DockerAdapter, container_id: str, repo_url: str
) -> None:
    """Clone the repo via SSH inside the container (D4, EC-8)."""
    exit_code, output = adapter.exec(
        container_id,
        ["git", "clone", repo_url, "/workspace/repo"],
    )
    if exit_code != 0:
        raise LaunchError(f"SSH clone failed for {repo_url}: {output}")


def _validate_model_credentials(
    adapter: DockerAdapter, container_id: str, provider_id: str
) -> None:
    """Check the provider appears in `opencode models` output (EC-9, Q-7)."""
    exit_code, output = adapter.exec(container_id, ["opencode", "models"])
    if exit_code != 0:
        raise LaunchError(
            f"Model credential validation failed: cannot run 'opencode models': {output}"
        )
    if provider_id not in output:
        raise LaunchError(
            f"Credential missing for provider '{provider_id}'"
        )


def launch_agent(
    conn: sqlite3.Connection,
    spec: LaunchSpec,
    adapter: DockerAdapter,
) -> LaunchResult:
    """Full launch sequence: preflight → container → clone → validate → persist."""
    # --- Preflight: image availability (AC-11, EC-7) ---
    _validate_image(adapter, spec.docker_image_name)

    # --- Create agent_instance record first so we have an ID for labels ---
    instance = repos.agent_instance_create(
        conn,
        project_id=spec.project_id,
        agent_type=spec.agent_type,
        agent_definition_id=spec.agent_definition_id,
        model_entry_id=spec.model_entry_id,
        docker_image_id=spec.docker_image_id,
    )

    name = _container_name(spec.project_id, spec.agent_type)
    labels = _labels(spec.project_id, spec.agent_type, instance.id)
    env, volumes, _temp_paths = _materialize_credential_files(spec)

    # --- Create+start container ---
    try:
        container_id = adapter.run(
            image=spec.docker_image_name,
            name=name,
            env=env,
            volumes=volumes,
            labels=labels,
        )
    except DockerError as exc:
        repos.agent_instance_update(conn, instance.id, status="failed")
        raise LaunchError(f"Failed to start container: {exc}") from exc

    # --- Clone repo via SSH (EC-8) ---
    try:
        _clone_repo(adapter, container_id, spec.repo_url)
    except LaunchError:
        adapter.stop(container_id)
        adapter.remove(container_id)
        repos.agent_instance_update(conn, instance.id, status="failed")
        raise

    # --- Validate model credentials (EC-9) ---
    try:
        _validate_model_credentials(adapter, container_id, spec.model_provider_id)
    except LaunchError:
        adapter.stop(container_id)
        adapter.remove(container_id)
        repos.agent_instance_update(conn, instance.id, status="failed")
        raise

    # --- Persist container metadata + launch config snapshot ---
    config_snapshot = {
        "agent_definition_id": spec.agent_definition_id,
        "agent_name": spec.agent_name,
        "github_identity": spec.github_identity,
        "model_entry_id": spec.model_entry_id,
        "model_provider_id": spec.model_provider_id,
        "model_id": spec.model_id,
        "docker_image_id": spec.docker_image_id,
        "docker_image_name": spec.docker_image_name,
        "container_name": name,
        "container_labels": labels,
        "repo_url": spec.repo_url,
        "proxy": {
            "http": spec.http_proxy,
            "https": spec.https_proxy,
        },
    }
    repos.config_snapshot_create(conn, instance.id, config_snapshot)
    repos.agent_instance_update(
        conn, instance.id, container_id=container_id, container_name=name
    )

    return LaunchResult(container_id=container_id, container_name=name, labels=labels)


def cleanup_credential_files(paths: list[Path]) -> None:
    """Remove temp credential files after launch (or on failure)."""
    for path in paths:
        try:
            if path.is_dir():
                # ponytail: shutil.rmtree would be cleaner but unlink + rmdir is stdlib-minimal
                for child in path.iterdir():
                    child.unlink()
                path.rmdir()
            else:
                path.unlink()
        except OSError:
            pass
