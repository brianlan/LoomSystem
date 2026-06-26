from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from app import repositories as repos
from app.docker import DockerAdapter, LaunchError
from app.repositories import AgentInstance

_CONTAINER_PATH = "/loom"
_AGENT_PROMPT_PATH = Path(_CONTAINER_PATH) / "agent.md"
_SSH_KEY_PATH = Path(_CONTAINER_PATH) / "ssh_key"
_PROVIDER_CONFIG_PATH = Path(_CONTAINER_PATH) / "provider_config.json"
_REPO_PATH = Path(_CONTAINER_PATH) / "repo"


class LaunchService:
    """Materialize and launch an agent container for a project."""

    def __init__(self, adapter: DockerAdapter, workspace_root: Path | None = None) -> None:
        self.adapter = adapter
        self.workspace_root = workspace_root or Path(tempfile.gettempdir()) / "loomsystem"

    def launch(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: int,
        agent_type: str,
        agent_definition_id: int,
        model_entry_id: int,
        docker_image_id: int,
        issue_number: int | None = None,
    ) -> AgentInstance:
        if agent_type not in {"reviewer", "implementor"}:
            raise LaunchError(f"Invalid agent type {agent_type!r}")

        project = repos.project_get(conn, project_id)
        if not project:
            raise LaunchError(f"Project {project_id} not found")
        agent_definition = repos.agent_definition_get(conn, agent_definition_id)
        if not agent_definition:
            raise LaunchError(f"Agent definition {agent_definition_id} not found")
        model_entry = repos.model_entry_get(conn, model_entry_id)
        if not model_entry:
            raise LaunchError(f"Model entry {model_entry_id} not found")
        docker_image = repos.docker_image_get(conn, docker_image_id)
        if not docker_image:
            raise LaunchError(f"Docker image {docker_image_id} not found")

        ssh_key = repos.setting_get(conn, "ssh_key") or ""
        if not ssh_key:
            raise LaunchError("SSH key not configured")

        instance = repos.agent_instance_create(
            conn,
            project_id=project_id,
            agent_type=agent_type,
            agent_definition_id=agent_definition_id,
            model_entry_id=model_entry_id,
            docker_image_id=docker_image_id,
            issue_number=issue_number,
        )
        conn.commit()

        workspace_dir = self._workspace_dir(instance.id)
        container_id: str | None = None
        try:
            snapshot = self._build_snapshot(
                project_id=project_id,
                agent_type=agent_type,
                agent_definition_id=agent_definition_id,
                model_entry_id=model_entry_id,
                docker_image_id=docker_image_id,
                docker_image_name=docker_image.image_name,
                issue_number=issue_number,
                repo_url=project.repo_url,
                proxy=self._proxy_settings(conn),
            )
            repos.config_snapshot_create(conn, instance.id, snapshot)

            self._materialize(
                workspace_dir,
                prompt_markdown=agent_definition.prompt_markdown,
                ssh_key=ssh_key,
                provider_config=model_entry.custom_config or {},
            )

            if not self.adapter.image_exists(docker_image.image_name):
                self.adapter.pull(docker_image.image_name)

            labels = _container_labels(project_id, agent_type, instance.id)
            name = _container_name(project_id, agent_type, instance.id)
            env = self._build_env(
                project=project,
                agent_type=agent_type,
                instance_id=instance.id,
                issue_number=issue_number,
                model_credentials=model_entry.credentials,
                proxy=snapshot.get("proxy") or {},
            )
            container_id = self.adapter.run(
                docker_image.image_name,
                name=name,
                labels=labels,
                env=env,
                volumes=[(str(workspace_dir), _CONTAINER_PATH)],
            )

            self._validate_clone(container_id, project.repo_url)
            self._validate_credentials(container_id)

            repos.agent_instance_update(
                conn,
                instance.id,
                container_id=container_id,
                status="running",
            )
            conn.commit()
            return repos.agent_instance_get(conn, instance.id)  # type: ignore[return-value]
        except Exception:
            if container_id:
                self.adapter.stop(container_id)
                self.adapter.remove(container_id)
            repos.agent_instance_update(conn, instance.id, status="error")
            conn.commit()
            shutil.rmtree(workspace_dir, ignore_errors=True)
            raise

    def _workspace_dir(self, instance_id: int) -> Path:
        path = self.workspace_root / "launches" / str(instance_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _build_snapshot(
        self,
        *,
        project_id: int,
        agent_type: str,
        agent_definition_id: int,
        model_entry_id: int,
        docker_image_id: int,
        docker_image_name: str,
        issue_number: int | None,
        repo_url: str,
        proxy: dict[str, str | None],
    ) -> dict:
        return {
            "project_id": project_id,
            "agent_type": agent_type,
            "agent_definition_id": agent_definition_id,
            "model_entry_id": model_entry_id,
            "docker_image_id": docker_image_id,
            "docker_image_name": docker_image_name,
            "issue_number": issue_number,
            "repo_url": repo_url,
            "proxy": proxy,
        }

    def _proxy_settings(self, conn: sqlite3.Connection) -> dict[str, str | None]:
        return {
            "http_proxy": repos.setting_get(conn, "http_proxy") or None,
            "https_proxy": repos.setting_get(conn, "https_proxy") or None,
        }

    def _materialize(
        self,
        workspace_dir: Path,
        *,
        prompt_markdown: str,
        ssh_key: str,
        provider_config: dict,
    ) -> None:
        (workspace_dir / "agent.md").write_text(prompt_markdown)

        ssh_path = workspace_dir / "ssh_key"
        ssh_path.write_text(ssh_key)
        ssh_path.chmod(0o600)

        (workspace_dir / "provider_config.json").write_text(json.dumps(provider_config))

    def _build_env(
        self,
        *,
        project: repos.Project,
        agent_type: str,
        instance_id: int,
        issue_number: int | None,
        model_credentials: str,
        proxy: dict[str, str | None],
    ) -> dict[str, str]:
        env: dict[str, str] = {
            "LOOM_PROJECT_ID": str(project.id),
            "LOOM_AGENT_TYPE": agent_type,
            "LOOM_AGENT_INSTANCE_ID": str(instance_id),
            "LOOM_REPO_URL": project.repo_url,
            "LOOM_SSH_KEY_PATH": str(_SSH_KEY_PATH),
            "LOOM_AGENT_PROMPT_PATH": str(_AGENT_PROMPT_PATH),
            "LOOM_PROVIDER_CONFIG_PATH": str(_PROVIDER_CONFIG_PATH),
            "LOOM_MODEL_CREDENTIALS": model_credentials,
            "GIT_SSH_COMMAND": (
                f"ssh -i {_SSH_KEY_PATH} -o StrictHostKeyChecking=no "
                "-o UserKnownHostsFile=/dev/null"
            ),
        }
        if issue_number is not None:
            env["LOOM_ISSUE_NUMBER"] = str(issue_number)
        for key in ("http_proxy", "https_proxy"):
            value = proxy.get(key)
            if value:
                env[key] = value
                env[key.upper()] = value
        return env

    def _validate_clone(self, container_id: str, repo_url: str) -> None:
        exit_code, output = self.adapter.exec(
            container_id,
            ["git", "clone", "--depth", "1", repo_url, str(_REPO_PATH)],
        )
        if exit_code != 0:
            raise LaunchError(f"SSH clone failed: {output.strip()}")

    def _validate_credentials(self, container_id: str) -> None:
        exit_code, output = self.adapter.exec(
            container_id,
            ["sh", "-c", 'test -n "$LOOM_MODEL_CREDENTIALS"'],
        )
        if exit_code != 0:
            raise LaunchError(f"Model credentials not reachable: {output.strip()}")


def _container_name(project_id: int, agent_type: str, instance_id: int) -> str:
    return f"loom-{project_id}-{agent_type}-{instance_id}"


def _container_labels(project_id: int, agent_type: str, instance_id: int) -> dict[str, str]:
    return {
        "loomsystem.managed": "true",
        "loomsystem.project_id": str(project_id),
        "loomsystem.agent_type": agent_type,
        "loomsystem.agent_instance_id": str(instance_id),
    }
