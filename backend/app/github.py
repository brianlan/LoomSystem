"""GitHub adapter: repo URL parsing and an HTTP-backed issue/PR fetcher.

The adapter is a thin stdlib-only wrapper over the GitHub REST API so it can be
mocked in tests. No webhook code lives here (NG-3: all state is polling-derived).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class GitHubError(Exception):
    """Raised when the app-level GitHub token is invalid or GitHub rejects us."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind  # "invalid_token" | "rate_limited" | "error"


@dataclass(frozen=True)
class IssueDTO:
    number: int
    title: str
    state: str


@dataclass(frozen=True)
class PullRequestDTO:
    number: int
    title: str
    state: str
    merged: bool
    body: str | None = None


# Matches git@github.com:owner/repo(.git)? and https://github.com/owner/repo(.git)?
_REPO_URL_RE = re.compile(
    r"(?:git@github\.com:|https?://github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def parse_repo_url(url: str) -> tuple[str, str]:
    """Return (owner, repo) for a GitHub repo URL, or raise GitHubError."""
    match = _REPO_URL_RE.match(url.strip())
    if not match:
        raise GitHubError(f"Unparseable GitHub repo URL: {url!r}", kind="error")
    return match.group("owner"), match.group("repo")


class GitHubAdapter(Protocol):
    def list_open_issues(self, owner: str, repo: str) -> list[IssueDTO]: ...

    def list_open_pull_requests(self, owner: str, repo: str) -> list[PullRequestDTO]: ...

    def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequestDTO: ...

    def get_issue(self, owner: str, repo: str, number: int) -> IssueDTO: ...

    def update_pr(self, owner: str, repo: str, number: int, body: str) -> None: ...


class HttpGitHubAdapter:
    """GitHub REST API adapter backed by urllib. Uses the app-level token."""

    def __init__(
        self,
        token: str,
        proxy_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._timeout = timeout
        if proxy_url:
            self._opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
        else:
            self._opener = urllib.request.build_opener()

    def _request(
        self, url: str, method: str = "GET", json_body: dict[str, object] | None = None
    ) -> object:
        if not self._token:
            raise GitHubError("App-level GitHub token is not configured", kind="invalid_token")
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            data=data,
            method=method,
        )
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # 403 is also returned for rate limits; GitHub sets X-RateLimit-Remaining: 0.
                remaining = exc.headers.get("X-RateLimit-Remaining")
                kind = "rate_limited" if remaining == "0" else "invalid_token"
                raise GitHubError(
                    f"GitHub API rejected the app-level token (HTTP {exc.code})", kind=kind
                ) from exc
            raise GitHubError(f"GitHub API HTTP {exc.code}", kind="error") from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"GitHub API unreachable: {exc.reason}", kind="error") from exc

    def list_open_issues(self, owner: str, repo: str) -> list[IssueDTO]:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues?state=open&per_page=100"
        data = self._request(url)
        if not isinstance(data, list):
            raise GitHubError("Unexpected non-list response from issues endpoint", kind="error")
        return [
            IssueDTO(number=item["number"], title=item["title"], state=item["state"])
            for item in data
            if "pull_request" not in item  # issues endpoint also returns PRs
        ]

    def list_open_pull_requests(self, owner: str, repo: str) -> list[PullRequestDTO]:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&per_page=100"
        data = self._request(url)
        if not isinstance(data, list):
            raise GitHubError("Unexpected non-list response from pulls endpoint", kind="error")
        return [
            PullRequestDTO(
                number=item["number"],
                title=item["title"],
                state=item["state"],
                merged=False,  # open PRs are never merged
                body=item.get("body"),
            )
            for item in data
        ]

    def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequestDTO:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
        item = self._request(url)
        if not isinstance(item, dict):
            raise GitHubError(f"Unexpected non-object response for PR {number}", kind="error")
        return PullRequestDTO(
            number=item["number"],
            title=item["title"],
            state=item["state"],
            merged=bool(item.get("merged")),
            body=item.get("body"),
        )

    def update_pr(self, owner: str, repo: str, number: int, body: str) -> None:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
        self._request(url, method="PATCH", json_body={"body": body})

    def get_issue(self, owner: str, repo: str, number: int) -> IssueDTO:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
        item = self._request(url)
        if not isinstance(item, dict):
            raise GitHubError(f"Unexpected non-object response for issue {number}", kind="error")
        return IssueDTO(number=item["number"], title=item["title"], state=item["state"])
