from unittest import mock

import pytest

from app.docker import CliDockerAdapter, FakeDockerAdapter, LaunchError


def test_fake_image_exists_records_call_and_honors_state() -> None:
    adapter = FakeDockerAdapter()
    assert not adapter.image_exists("alpine")
    adapter.mark_image_present("alpine")
    assert adapter.image_exists("alpine")
    assert adapter.calls == [
        ("image_exists", ("alpine",), {}),
        ("image_exists", ("alpine",), {}),
    ]


def test_fake_pull_adds_image_or_fails() -> None:
    adapter = FakeDockerAdapter()
    adapter.pull("alpine")
    assert "alpine" in adapter.images

    adapter.set_pull_fail()
    with pytest.raises(LaunchError):
        adapter.pull("busybox")


def test_fake_run_records_container_config() -> None:
    adapter = FakeDockerAdapter()
    cid = adapter.run(
        "alpine",
        name="loom-1-reviewer-2",
        labels={"loomsystem.managed": "true"},
        env={"LOOM_X": "y"},
        volumes=[("/host", "/container")],
        command=["sleep", "1"],
    )
    assert cid in adapter.containers
    container = adapter.containers[cid]
    assert container["name"] == "loom-1-reviewer-2"
    assert container["labels"]["loomsystem.managed"] == "true"
    assert container["env"]["LOOM_X"] == "y"
    assert container["volumes"] == [("/host", "/container")]
    assert container["command"] == ["sleep", "1"]


def test_fake_exec_uses_configured_result() -> None:
    adapter = FakeDockerAdapter()
    cid = adapter.run("alpine", name="c", labels={}, env={}, volumes=[])
    adapter.set_exec_result(cid, ["git", "clone"], 1, "auth fail")
    exit_code, output = adapter.exec(cid, ["git", "clone"])
    assert exit_code == 1
    assert output == "auth fail"


def test_fake_stop_and_remove_update_state() -> None:
    adapter = FakeDockerAdapter()
    cid = adapter.run("alpine", name="c", labels={}, env={}, volumes=[])
    assert adapter.containers[cid]["running"]
    adapter.stop(cid)
    assert not adapter.containers[cid]["running"]
    adapter.remove(cid)
    assert cid not in adapter.containers


def test_cli_image_exists_invokes_docker_inspect() -> None:
    adapter = CliDockerAdapter()
    with mock.patch("app.docker.subprocess.run") as run_mock:
        run_mock.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
        assert adapter.image_exists("alpine")
        run_mock.assert_called_once()
        args = run_mock.call_args[0][0]
        assert args == ["docker", "image", "inspect", "alpine"]


def test_cli_run_command_construction() -> None:
    adapter = CliDockerAdapter()
    with mock.patch("app.docker.subprocess.run") as run_mock:
        run_mock.return_value = mock.MagicMock(returncode=0, stdout="cid123\n", stderr="")
        cid = adapter.run(
            "alpine",
            name="loom-1-reviewer-2",
            labels={"loomsystem.managed": "true", "loomsystem.project_id": "1"},
            env={"LOOM_X": "y"},
            volumes=[("/host", "/container")],
        )
    assert cid == "cid123"
    args = run_mock.call_args[0][0]
    assert args[0:3] == ["docker", "run", "-d"]
    assert "--name" in args
    assert "loom-1-reviewer-2" in args
    assert "--label" in args
    assert "loomsystem.managed=true" in args
    assert "-e" in args
    assert "LOOM_X=y" in args
    assert "-v" in args
    assert "/host:/container" in args
    assert "alpine" in args
