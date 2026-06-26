"""Tests for the triage LLM service."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app import repositories as repos
from app.db import get_db
from app.github import IssueDTO
from app.polling import poll_project
from app.triage import (
    TriageConfig,
    TriageError,
    build_prompt,
    load_triage_config,
    parse_ranking,
    rank_issues,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "triage_test.db"
    get_db(path).reset()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    db = get_db(db_path)
    c = db.connect()
    yield c
    c.close()


def _seed_project_with_issues(conn: sqlite3.Connection) -> repos.Project:
    """Create a project, seed triage config, and poll some open issues into snapshots."""
    project = repos.project_create(conn, "demo", "git@github.com:brianlan/demo.git")
    # Seed triage config.
    repos.setting_set(conn, "triage_endpoint_url", "https://api.example.com/v1/chat/completions")
    repos.setting_set(conn, "triage_model_name", "gpt-4")
    repos.setting_set(conn, "triage_api_key", "sk-triage-key")
    repos.setting_set(conn, "triage_headers", '{"X-Custom": "val"}')
    # Seed open issues via the polling service (from issue #5).
    class _FakeAdapter:
        def list_open_issues(self, owner: str, repo: str) -> list[IssueDTO]:
            return [
                IssueDTO(number=3, title="Bug in parser", state="open"),
                IssueDTO(number=1, title="Setup CI", state="open"),
                IssueDTO(number=5, title="Add tests", state="open"),
            ]

        def list_open_pull_requests(self, owner: str, repo: str) -> list[Any]:
            return []

        def get_pull_request(self, owner: str, repo: str, number: int) -> Any:
            raise NotImplementedError

        def get_issue(self, owner: str, repo: str, number: int) -> Any:
            raise NotImplementedError

    poll_project(conn, project, _FakeAdapter())
    conn.commit()
    return project


class _FakeTriageClient:
    """Fake triage client that returns a configured response or raises."""

    def __init__(
        self,
        response: str | None = None,
        error: Exception | None = None,
        responses: list[str] | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self._responses = responses
        self._call_index = 0

    def call(self, config: TriageConfig, prompt: str) -> str:
        if self._error:
            raise self._error
        if self._responses:
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp
        assert self._response is not None
        return self._response


# ---------------------------------------------------------------------------
# Prompt construction tests
# ---------------------------------------------------------------------------


def test_build_prompt_includes_all_issues() -> None:
    snapshot = [
        {"issue_number": 3, "title": "Bug", "state": "open"},
        {"issue_number": 1, "title": "CI", "state": "open"},
    ]
    prompt = build_prompt(snapshot)
    assert "#3" in prompt
    assert "Bug" in prompt
    assert "#1" in prompt
    assert "CI" in prompt
    assert "ranked_issue_numbers" in prompt


def test_build_prompt_empty_issues() -> None:
    prompt = build_prompt([])
    assert "ranked_issue_numbers" in prompt


# ---------------------------------------------------------------------------
# Ranking parser tests
# ---------------------------------------------------------------------------


def test_parse_valid_ranking() -> None:
    raw = '{"ranked_issue_numbers": [3, 1, 5]}'
    assert parse_ranking(raw) == [3, 1, 5]


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(TriageError, match="not valid JSON"):
        parse_ranking("not json at all")


def test_parse_missing_key_raises() -> None:
    with pytest.raises(TriageError, match="missing 'ranked_issue_numbers'"):
        parse_ranking('{"other": [1, 2]}')


def test_parse_non_list_raises() -> None:
    with pytest.raises(TriageError, match="missing 'ranked_issue_numbers'"):
        parse_ranking('{"ranked_issue_numbers": "not a list"}')


def test_parse_non_int_elements_raises() -> None:
    with pytest.raises(TriageError, match="must contain only integers"):
        parse_ranking('{"ranked_issue_numbers": [1, "two", 3]}')


def test_parse_non_object_raises() -> None:
    with pytest.raises(TriageError, match="not a JSON object"):
        parse_ranking("[1, 2, 3]")


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


def test_load_triage_config_success(conn: sqlite3.Connection) -> None:
    repos.setting_set(conn, "triage_endpoint_url", "https://api.example.com")
    repos.setting_set(conn, "triage_model_name", "gpt-4")
    repos.setting_set(conn, "triage_api_key", "sk-key")
    repos.setting_set(conn, "triage_headers", '{"X-Hdr": "v"}')

    config = load_triage_config(conn)
    assert config.endpoint_url == "https://api.example.com"
    assert config.model_name == "gpt-4"
    assert config.api_key == "sk-key"
    assert config.headers == {"X-Hdr": "v"}


def test_load_triage_config_missing_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(TriageError, match="incomplete"):
        load_triage_config(conn)


# ---------------------------------------------------------------------------
# rank_issues tests
# ---------------------------------------------------------------------------


def test_rank_issues_happy_path(conn: sqlite3.Connection) -> None:
    project = _seed_project_with_issues(conn)
    client = _FakeTriageClient(response='{"ranked_issue_numbers": [5, 3, 1]}')

    ranking = rank_issues(conn, project.id, client, sleep=lambda _: None)

    assert ranking == [5, 3, 1]
    # Audit record persisted.
    run = repos.triage_run_latest_full(conn, project.id)
    assert run is not None
    assert run.status == "success"
    assert run.attempts == 1
    assert run.ranked_issue_ids == [5, 3, 1]
    assert run.raw_response == '{"ranked_issue_numbers": [5, 3, 1]}'
    assert run.input_snapshot is not None
    assert len(run.input_snapshot) == 3
    assert run.error is None


def test_rank_issues_retries_on_malformed_then_succeeds(conn: sqlite3.Connection) -> None:
    project = _seed_project_with_issues(conn)
    client = _FakeTriageClient(
        responses=[
            "not json",  # attempt 1 fails
            '{"ranked_issue_numbers": [1, 3, 5]}',  # attempt 2 succeeds
        ]
    )

    ranking = rank_issues(conn, project.id, client, sleep=lambda _: None)

    assert ranking == [1, 3, 5]
    run = repos.triage_run_latest_full(conn, project.id)
    assert run is not None
    assert run.status == "success"
    assert run.attempts == 2


def test_rank_issues_fails_after_max_attempts(conn: sqlite3.Connection) -> None:
    project = _seed_project_with_issues(conn)
    client = _FakeTriageClient(error=TriageError("LLM HTTP 500: Internal Server Error"))

    with pytest.raises(TriageError, match="failed after 10 attempts"):
        rank_issues(conn, project.id, client, sleep=lambda _: None)

    # Failed audit record persisted.
    run = repos.triage_run_latest_full(conn, project.id)
    assert run is not None
    assert run.status == "failed"
    assert run.attempts == 10
    assert run.ranked_issue_ids == []
    assert run.error is not None
    assert "HTTP 500" in run.error

    # Notification created (FR-35).
    notifications = repos.notification_list(conn, project_id=project.id)
    assert len(notifications) == 1
    assert "Triage failed" in notifications[0]["message"]


def test_rank_issues_persists_input_snapshot(conn: sqlite3.Connection) -> None:
    project = _seed_project_with_issues(conn)
    client = _FakeTriageClient(response='{"ranked_issue_numbers": [3]}')

    rank_issues(conn, project.id, client, sleep=lambda _: None)

    run = repos.triage_run_latest_full(conn, project.id)
    assert run is not None
    assert run.input_snapshot is not None
    numbers = {item["issue_number"] for item in run.input_snapshot}
    assert numbers == {1, 3, 5}


def test_rank_issues_exponential_backoff_delay(conn: sqlite3.Connection) -> None:
    """Verify exponential backoff is called with increasing delays."""
    project = _seed_project_with_issues(conn)
    client = _FakeTriageClient(
        responses=[
            "bad",  # fail
            "also bad",  # fail
            '{"ranked_issue_numbers": [1]}',  # success
        ]
    )
    delays: list[float] = []
    rank_issues(conn, project.id, client, sleep=delays.append)

    # First retry: 1.0, second retry: 2.0
    assert delays == [1.0, 2.0]


def test_rank_issues_no_open_issues_still_calls_llm(conn: sqlite3.Connection) -> None:
    """Even with zero open issues, triage calls the LLM (empty input snapshot)."""
    project = repos.project_create(conn, "empty", "git@github.com:brianlan/empty.git")
    repos.setting_set(conn, "triage_endpoint_url", "https://api.example.com")
    repos.setting_set(conn, "triage_model_name", "gpt-4")
    repos.setting_set(conn, "triage_api_key", "sk-key")
    repos.setting_set(conn, "triage_headers", "{}")
    client = _FakeTriageClient(response='{"ranked_issue_numbers": []}')

    ranking = rank_issues(conn, project.id, client, sleep=lambda _: None)

    assert ranking == []
    run = repos.triage_run_latest_full(conn, project.id)
    assert run is not None
    assert run.input_snapshot == []
