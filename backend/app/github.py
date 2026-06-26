from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

GITHUB_API_BASE = "https://api.github.com"


class GitHubError(Exception):
    pass


class GitHubAuthError(GitHubError):
    pass


class GitHubRateLimitError(GitHubError):
    pass


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str


@dataclass(frozen=True)
class GitHubIssue:
    number: int
    title: str
    state: str
    is_open: bool
    assignees: list[str]
    labels: list[str]
    raw: dict[str, Any]


@dataclass(frozen=True)
class GitHubPullRequest:
    number: int
    title: str
    state: str
    is_open: bool
    is_draft: bool
    is_merged: bool
    merged_at: str | None
    raw: dict[str, Any]


def parse_repo_url(repo_url: str) -> RepoRef:
    """Extract owner/repo from a GitHub repository URL.

    Supports https://github.com/owner/repo and http variants,
    with optional trailing slash or .git suffix.
    """
    parsed = urlparse(repo_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise GitHubError(f"Unsupported repository URL scheme: {parsed.scheme}")
    if parsed.hostname not in {None, "github.com", "www.github.com"}:
        raise GitHubError(f"Unsupported repository host: {parsed.hostname}")

    path = parsed.path.strip("/").removesuffix(".git")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise GitHubError(f"Invalid repository URL path: {parsed.path}")

    owner, repo = parts[0], parts[1]
    if not re.match(r"^[A-Za-z0-9_.-]+$", owner) or not re.match(r"^[A-Za-z0-9_.-]+$", repo):
        raise GitHubError(f"Invalid owner or repo name in URL: {repo_url}")

    return RepoRef(owner=owner, repo=repo)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise GitHubAuthError(f"GitHub token invalid or revoked: {response.status_code}")
    if response.status_code == 403:
        # Rate-limit responses are still 403; detect by header or body.
        if (
            response.headers.get("x-ratelimit-remaining") == "0"
            or "rate limit" in response.text.lower()
        ):
            raise GitHubRateLimitError("GitHub API rate limit exceeded")
        raise GitHubAuthError(f"GitHub token invalid or revoked: {response.status_code}")
    if response.status_code == 404:
        raise GitHubError(f"GitHub repository not found: {response.status_code}")
    response.raise_for_status()


def _issue_from_raw(raw: dict[str, Any]) -> GitHubIssue:
    return GitHubIssue(
        number=raw["number"],
        title=raw.get("title", ""),
        state=raw["state"],
        is_open=raw.get("state") == "open",
        assignees=[u.get("login", "") for u in raw.get("assignees", [])],
        labels=[label.get("name", "") for label in raw.get("labels", [])],
        raw=raw,
    )


def _pr_from_raw(raw: dict[str, Any]) -> GitHubPullRequest:
    return GitHubPullRequest(
        number=raw["number"],
        title=raw.get("title", ""),
        state=raw["state"],
        is_open=raw.get("state") == "open",
        is_draft=raw.get("draft", False),
        is_merged=raw.get("merged", False),
        merged_at=raw.get("merged_at"),
        raw=raw,
    )


class GitHubAdapter:
    """Sync GitHub API adapter. Mock-friendly: callers can subclass for tests."""

    def __init__(self, api_base: str = GITHUB_API_BASE) -> None:
        self.api_base = api_base

    def _get(self, path: str, token: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(base_url=self.api_base, headers=headers, timeout=30.0) as client:
            response = client.get(path)
            _raise_for_status(response)
            return response.json()

    def list_open_issues(self, ref: RepoRef, token: str) -> list[GitHubIssue]:
        raw_items = self._get(
            f"/repos/{ref.owner}/{ref.repo}/issues?state=open&per_page=100",
            token,
        )
        # The issues endpoint also returns pull requests; filter them out.
        return [
            _issue_from_raw(item)
            for item in raw_items
            if "pull_request" not in item
        ]

    def list_open_prs(self, ref: RepoRef, token: str) -> list[GitHubPullRequest]:
        raw_items = self._get(
            f"/repos/{ref.owner}/{ref.repo}/pulls?state=open&per_page=100",
            token,
        )
        return [_pr_from_raw(item) for item in raw_items]

    def fetch_pr(self, ref: RepoRef, pr_number: int, token: str) -> GitHubPullRequest:
        raw = self._get(f"/repos/{ref.owner}/{ref.repo}/pulls/{pr_number}", token)
        return _pr_from_raw(raw)
