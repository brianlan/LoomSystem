import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from app.github import (
    GitHubError,
    HttpGitHubAdapter,
    IssueDTO,
    PullRequestDTO,
    parse_repo_url,
)


def test_parse_ssh_url() -> None:
    assert parse_repo_url("git@github.com:brianlan/LoomSystem.git") == ("brianlan", "LoomSystem")


def test_parse_https_url() -> None:
    assert parse_repo_url("https://github.com/brianlan/LoomSystem") == ("brianlan", "LoomSystem")


def test_parse_https_url_with_git_suffix() -> None:
    assert parse_repo_url("https://github.com/brianlan/LoomSystem.git/") == (
        "brianlan",
        "LoomSystem",
    )


def test_parse_invalid_url_raises() -> None:
    with pytest.raises(GitHubError):
        parse_repo_url("not a github url")


def _fake_response(body: bytes, status: int = 200, headers: dict | None = None):
    msg = BytesIO(body)

    class _Resp:
        def __init__(self) -> None:
            self.headers = headers or {}

        def read(self) -> bytes:
            return msg.getvalue()

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    return _Resp()


def test_list_open_issues_filters_out_prs() -> None:
    payload = (
        b'[{"number":1,"title":"issue","state":"open"},'
        b'{"number":2,"title":"pr","state":"open","pull_request":{}}]'
    )
    adapter = HttpGitHubAdapter(token="t")
    with patch.object(adapter._opener, "open", return_value=_fake_response(payload)):
        issues = adapter.list_open_issues("o", "r")
    assert issues == [IssueDTO(number=1, title="issue", state="open")]


def test_list_open_pull_requests() -> None:
    payload = b'[{"number":5,"title":"pr","state":"open"}]'
    adapter = HttpGitHubAdapter(token="t")
    with patch.object(adapter._opener, "open", return_value=_fake_response(payload)):
        prs = adapter.list_open_pull_requests("o", "r")
    assert prs == [PullRequestDTO(number=5, title="pr", state="open", merged=False)]


def test_get_pull_request_merged_flag() -> None:
    payload = b'{"number":5,"title":"pr","state":"closed","merged":true}'
    adapter = HttpGitHubAdapter(token="t")
    with patch.object(adapter._opener, "open", return_value=_fake_response(payload)):
        pr = adapter.get_pull_request("o", "r", 5)
    assert pr.merged is True
    assert pr.state == "closed"


def test_missing_token_raises_invalid_token() -> None:
    adapter = HttpGitHubAdapter(token="")
    with pytest.raises(GitHubError) as exc_info:
        adapter.list_open_issues("o", "r")
    assert exc_info.value.kind == "invalid_token"


def test_http_401_surfaces_invalid_token() -> None:
    adapter = HttpGitHubAdapter(token="bad")
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, BytesIO(b"{}"))
    with patch.object(adapter._opener, "open", side_effect=err):
        with pytest.raises(GitHubError) as exc_info:
            adapter.list_open_issues("o", "r")
    assert exc_info.value.kind == "invalid_token"


def test_http_403_rate_limited_surfaces_rate_limited() -> None:
    adapter = HttpGitHubAdapter(token="t")
    headers = {"X-RateLimit-Remaining": "0"}
    err = urllib.error.HTTPError("url", 403, "Forbidden", headers, BytesIO(b"{}"))
    with patch.object(adapter._opener, "open", side_effect=err):
        with pytest.raises(GitHubError) as exc_info:
            adapter.list_open_pull_requests("o", "r")
    assert exc_info.value.kind == "rate_limited"
