from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from typing import Any


class LaunchError(Exception):
    """Raised when a Docker launch or preflight step fails."""


class DockerAdapter(ABC):
    """Abstract adapter for Docker pull/run/exec/stop/remove/inspect operations."""

    @abstractmethod
    def image_exists(self, image_name: str) -> bool:
        """Return True if the image is present locally."""

    @abstractmethod
    def pull(self, image_name: str) -> None:
        """Pull *image_name* from the registry."""

    @abstractmethod
    def run(
        self,
        image_name: str,
        *,
        name: str,
        labels: dict[str, str],
        env: dict[str, str],
        volumes: list[tuple[str, str]],
        command: list[str] | None = None,
    ) -> str:
        """Run a container and return its ID."""

    @abstractmethod
    def exec(self, container_id: str, command: list[str]) -> tuple[int, str]:
        """Execute *command* in *container_id* and return (exit_code, output)."""

    @abstractmethod
    def stop(self, container_id: str) -> None:
        """Stop *container_id*."""

    @abstractmethod
    def remove(self, container_id: str) -> None:
        """Remove *container_id*."""

    @abstractmethod
    def inspect(self, container_id: str) -> dict[str, Any]:
        """Return low-level container info."""


class CliDockerAdapter(DockerAdapter):
    """Docker adapter backed by the local ``docker`` CLI."""

    def __init__(self, executable: str = "docker") -> None:
        self.executable = executable

    def _run(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self.executable, *args]
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=check,
        )

    def image_exists(self, image_name: str) -> bool:
        result = self._run(["image", "inspect", image_name], capture_output=True)
        return result.returncode == 0

    def pull(self, image_name: str) -> None:
        result = self._run(["pull", image_name], capture_output=True)
        if result.returncode != 0:
            raise LaunchError(f"Failed to pull image {image_name!r}")

    def run(
        self,
        image_name: str,
        *,
        name: str,
        labels: dict[str, str],
        env: dict[str, str],
        volumes: list[tuple[str, str]],
        command: list[str] | None = None,
    ) -> str:
        cmd: list[str] = [
            "run",
            "-d",
            "--name",
            name,
        ]
        for key, value in labels.items():
            cmd.extend(["--label", f"{key}={value}"])
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])
        for host_path, container_path in volumes:
            cmd.extend(["-v", f"{host_path}:{container_path}"])
        cmd.append(image_name)
        if command:
            cmd.extend(command)
        result = self._run(cmd, capture_output=True)
        if result.returncode != 0:
            raise LaunchError(f"Failed to run container {name!r}: {result.stderr.strip()}")
        return result.stdout.strip()

    def exec(self, container_id: str, command: list[str]) -> tuple[int, str]:
        result = self._run(
            ["exec", container_id, *command],
            capture_output=True,
        )
        return result.returncode, result.stdout

    def stop(self, container_id: str) -> None:
        self._run(["stop", container_id], capture_output=True)

    def remove(self, container_id: str) -> None:
        self._run(["rm", container_id], capture_output=True)

    def inspect(self, container_id: str) -> dict[str, Any]:
        result = self._run(
            ["inspect", container_id],
            capture_output=True,
        )
        if result.returncode != 0:
            return {}
        import json

        data = json.loads(result.stdout)
        return data[0] if data else {}


class FakeDockerAdapter(DockerAdapter):
    """In-memory Docker adapter for unit tests."""

    def __init__(self) -> None:
        self.images: set[str] = set()
        self.containers: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.pull_fail: bool = False
        self.run_fail: bool = False
        self._exec_results: dict[tuple[str, tuple[str, ...]], tuple[int, str]] = {}
        self._default_exec_results: dict[tuple[str, ...], tuple[int, str]] = {}
        self._counter = 0

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))

    def mark_image_present(self, image_name: str) -> None:
        self.images.add(image_name)

    def set_pull_fail(self, fail: bool = True) -> None:
        self.pull_fail = fail

    def set_run_fail(self, fail: bool = True) -> None:
        self.run_fail = fail

    def set_exec_result(
        self,
        container_id: str,
        command: list[str],
        exit_code: int,
        output: str = "",
    ) -> None:
        key = (container_id, tuple(command))
        self._exec_results[key] = (exit_code, output)

    def set_default_exec_result(
        self,
        command: list[str],
        exit_code: int,
        output: str = "",
    ) -> None:
        self._default_exec_results[tuple(command)] = (exit_code, output)

    def image_exists(self, image_name: str) -> bool:
        self._record("image_exists", image_name)
        return image_name in self.images

    def pull(self, image_name: str) -> None:
        self._record("pull", image_name)
        if self.pull_fail:
            raise LaunchError(f"Failed to pull image {image_name!r}")
        self.images.add(image_name)

    def run(
        self,
        image_name: str,
        *,
        name: str,
        labels: dict[str, str],
        env: dict[str, str],
        volumes: list[tuple[str, str]],
        command: list[str] | None = None,
    ) -> str:
        self._record("run", image_name, name=name, labels=labels, env=env, volumes=volumes)
        if self.run_fail:
            raise LaunchError(f"Failed to run container {name!r}")
        self._counter += 1
        container_id = f"container-{self._counter}"
        self.containers[container_id] = {
            "image": image_name,
            "name": name,
            "labels": dict(labels),
            "env": dict(env),
            "volumes": list(volumes),
            "command": list(command) if command else None,
            "running": True,
        }
        return container_id

    def exec(self, container_id: str, command: list[str]) -> tuple[int, str]:
        self._record("exec", container_id, command)
        key = (container_id, tuple(command))
        if key in self._exec_results:
            return self._exec_results[key]
        default_key = tuple(command)
        if default_key in self._default_exec_results:
            return self._default_exec_results[default_key]
        return 0, ""

    def stop(self, container_id: str) -> None:
        self._record("stop", container_id)
        if container_id in self.containers:
            self.containers[container_id]["running"] = False

    def remove(self, container_id: str) -> None:
        self._record("remove", container_id)
        self.containers.pop(container_id, None)

    def inspect(self, container_id: str) -> dict[str, Any]:
        self._record("inspect", container_id)
        return self.containers.get(container_id, {})
