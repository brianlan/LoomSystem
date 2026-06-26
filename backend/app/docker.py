"""Docker adapter: a testable wrapper over the `docker` CLI.

A Protocol lets tests inject a fake adapter without spawning processes. The
subprocess implementation wraps `docker pull/run/exec/stop/rm/inspect` via
:mod:`subprocess` — no SDK dependency (NG-4: single local daemon only).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Protocol


class DockerError(Exception):
    """Raised when a docker CLI operation fails."""


@dataclass(frozen=True)
class ContainerInfo:
    id: str
    name: str
    labels: dict[str, str]
    state: str


class DockerAdapter(Protocol):
    def image_exists(self, image: str) -> bool: ...

    def pull(self, image: str) -> None: ...

    def run(
        self,
        image: str,
        name: str,
        env: dict[str, str],
        volumes: list[str],
        labels: dict[str, str],
    ) -> str:
        """Create+start a detached container. Returns the container ID."""

    def exec(self, container_id: str, command: list[str]) -> tuple[int, str]:
        """Run a command inside a running container. Returns (exit_code, output)."""

    def stop(self, container_id: str) -> None: ...

    def remove(self, container_id: str) -> None: ...

    def inspect(self, container_id: str) -> ContainerInfo: ...


class SubprocessDockerAdapter:
    """Docker adapter backed by the `docker` CLI via subprocess."""

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 - args list, no shell
                ["docker", *args],
                capture_output=True,
                text=True,
                check=check,
            )
        except FileNotFoundError as exc:
            raise DockerError("docker CLI not found on PATH") from exc

    def image_exists(self, image: str) -> bool:
        result = self._run(["image", "inspect", image], check=False)
        return result.returncode == 0

    def pull(self, image: str) -> None:
        result = self._run(["pull", image], check=False)
        if result.returncode != 0:
            raise DockerError(f"docker pull failed for {image}: {result.stderr.strip()}")

    def run(
        self,
        image: str,
        name: str,
        env: dict[str, str],
        volumes: list[str],
        labels: dict[str, str],
    ) -> str:
        args = ["run", "-d", "--name", name]
        # ponytail: secrets travel through a 0600 --env-file (mkstemp) so they
        # never appear in the host `ps` argv or `docker inspect` env block.
        env_file: str | None = None
        if env:
            fd, env_file = tempfile.mkstemp(prefix="loom-env-", suffix=".env")
            with os.fdopen(fd, "w") as fh:
                for key, value in env.items():
                    fh.write(f"{key}={value}\n")
            args.extend(["--env-file", env_file])
        try:
            for vol in volumes:
                args.extend(["-v", vol])
            for key, value in labels.items():
                args.extend(["--label", f"{key}={value}"])
            args.append(image)
            result = self._run(args, check=False)
            if result.returncode != 0:
                raise DockerError(f"docker run failed: {result.stderr.strip()}")
            return result.stdout.strip()
        finally:
            if env_file:
                os.unlink(env_file)

    def exec(self, container_id: str, command: list[str]) -> tuple[int, str]:
        result = self._run(["exec", container_id, *command], check=False)
        output = (result.stdout + result.stderr).strip()
        return result.returncode, output

    def stop(self, container_id: str) -> None:
        self._run(["stop", container_id], check=False)

    def remove(self, container_id: str) -> None:
        self._run(["rm", "-f", container_id], check=False)

    def inspect(self, container_id: str) -> ContainerInfo:
        result = self._run(["inspect", container_id], check=False)
        if result.returncode != 0:
            raise DockerError(f"docker inspect failed: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        if not isinstance(data, list) or not data:
            raise DockerError(f"Unexpected inspect response for {container_id}")
        info = data[0]
        return ContainerInfo(
            id=info["Id"],
            name=info["Name"].lstrip("/"),
            labels=info.get("Config", {}).get("Labels", {}) or {},
            state=info.get("State", {}).get("Status", "unknown"),
        )


@dataclass
class FakeDockerAdapter:
    """In-memory docker adapter for tests. Records every call."""

    images: set[str] = field(default_factory=set)
    pull_failures: set[str] = field(default_factory=set)
    containers: dict[str, dict[str, str]] = field(default_factory=dict)
    exec_results: dict[tuple[str, str], tuple[int, str]] = field(default_factory=dict)
    exec_default: tuple[int, str] = (0, "")
    _counter: int = 0

    def image_exists(self, image: str) -> bool:
        return image in self.images

    def pull(self, image: str) -> None:
        if image in self.pull_failures:
            raise DockerError(f"docker pull failed for {image}: simulated failure")
        self.images.add(image)

    def run(
        self,
        image: str,
        name: str,
        env: dict[str, str],
        volumes: list[str],
        labels: dict[str, str],
    ) -> str:
        self._counter += 1
        cid = f"fake-container-{self._counter}"
        self.containers[cid] = {
            "image": image,
            "name": name,
            "env": json.dumps(env),
            "volumes": json.dumps(volumes),
            "labels": json.dumps(labels),
            "state": "running",
        }
        return cid

    def exec(self, container_id: str, command: list[str]) -> tuple[int, str]:
        key = (container_id, shlex.join(command))
        return self.exec_results.get(key, self.exec_default)

    def stop(self, container_id: str) -> None:
        if container_id in self.containers:
            self.containers[container_id]["state"] = "exited"

    def remove(self, container_id: str) -> None:
        self.containers.pop(container_id, None)

    def inspect(self, container_id: str) -> ContainerInfo:
        if container_id not in self.containers:
            raise DockerError(f"container {container_id} not found")
        c = self.containers[container_id]
        return ContainerInfo(
            id=container_id,
            name=c["name"],
            labels=json.loads(c["labels"]),
            state=c["state"],
        )
