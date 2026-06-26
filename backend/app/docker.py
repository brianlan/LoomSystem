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
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol


class DockerError(Exception):
    """Raised when a docker CLI operation fails."""


class ExecStream:
    """Line-buffered stdout/stderr stream from ``docker exec``.

    Iterate to consume output lines as they arrive (live console capture).
    ``exit_code`` is populated only after the stream is fully consumed; it stays
    ``None`` when the underlying process was lost before a clean exit, which the
    trigger service records as an incomplete trigger (EC-1/EC-2).
    """

    def __init__(self, lines: Iterable[str], finalize: Callable[[], int | None]) -> None:
        self._lines = lines
        self._finalize = finalize
        self.exit_code: int | None = None

    def __iter__(self) -> Iterator[str]:
        for line in self._lines:
            yield line
        self.exit_code = self._finalize()


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

    def exec_stream(self, container_id: str, command: list[str]) -> ExecStream:
        """Stream a command's stdout/stderr line-by-line (live capture).

        Returns an :class:`ExecStream`; iterate it for output lines and read
        ``.exit_code`` after exhaustion. Used by the trigger service (T07).
        """

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

    def exec_stream(self, container_id: str, command: list[str]) -> ExecStream:
        try:
            popen = subprocess.Popen(  # noqa: S603 - args list, no shell
                ["docker", "exec", container_id, *command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # line-buffered so console capture gets live chunks
            )
        except FileNotFoundError as exc:
            raise DockerError("docker CLI not found on PATH") from exc

        def finalize() -> int | None:
            popen.wait()
            return popen.returncode

        assert popen.stdout is not None
        return ExecStream(popen.stdout, finalize)

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
    # Streaming-exec results: (container_id, joined_cmd) -> (lines, exit_code|None).
    # exit_code None models a lost/incomplete stream (EC-1/EC-2).
    exec_stream_results: dict[tuple[str, str], tuple[list[str], int | None]] = field(
        default_factory=dict
    )
    # Default streaming result used when no exact-key match exists (test affordance).
    exec_stream_default: tuple[list[str], int | None] | None = None
    # Records every exec_stream invocation as (container_id, command).
    exec_stream_calls: list[tuple[str, list[str]]] = field(default_factory=list)
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

    def exec_stream(self, container_id: str, command: list[str]) -> ExecStream:
        self.exec_stream_calls.append((container_id, command))
        key = (container_id, shlex.join(command))
        if key in self.exec_stream_results:
            lines, exit_code = self.exec_stream_results[key]
        elif self.exec_stream_default is not None:
            lines, exit_code = self.exec_stream_default
        else:
            code, output = self.exec_results.get(key, self.exec_default)
            lines = [output] if output else []
            exit_code = code
        return ExecStream(lines, lambda: exit_code)

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
