"""Triage LLM service: ranks open issues using an OpenAI-compatible endpoint.

Calls the global triage LLM config (D18, D32), ranks all currently-open issues
for a project, retries up to 10 times with exponential backoff (D19, EC-4), and
persists full audit records (OBS-2): input snapshot, raw response, ranking,
attempts, and error.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from app import repositories as repos
from app.db import loads

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
INITIAL_BACKOFF_SECONDS = 1.0


class TriageError(Exception):
    """Raised when triage fails after all retries."""


@dataclass(frozen=True)
class TriageConfig:
    endpoint_url: str
    model_name: str
    api_key: str
    headers: dict[str, str]


def load_triage_config(conn: sqlite3.Connection) -> TriageConfig:
    """Read the global triage LLM config from settings (D18, D32)."""
    endpoint = repos.setting_get(conn, "triage_endpoint_url")
    model = repos.setting_get(conn, "triage_model_name")
    api_key = repos.setting_get(conn, "triage_api_key")
    headers = loads(repos.setting_get(conn, "triage_headers")) or {}
    if not endpoint or not model or not api_key:
        raise TriageError("Triage LLM config is incomplete (endpoint, model, or api_key missing)")
    return TriageConfig(
        endpoint_url=endpoint,
        model_name=model,
        api_key=api_key,
        headers=headers if isinstance(headers, dict) else {},
    )


def _build_input_snapshot(
    conn: sqlite3.Connection, project_id: int
) -> list[dict[str, Any]]:
    """Snapshot all open issues for the project (from GitHub polling snapshots)."""
    issues = repos.github_issue_list(conn, project_id)
    return [
        {"issue_number": i.issue_number, "title": i.title, "state": i.state}
        for i in issues
        if i.state == "open"
    ]


def build_prompt(input_snapshot: list[dict[str, Any]]) -> str:
    """Construct the triage prompt (Q-2: engineer-owned prompt contract)."""
    issue_lines = "\n".join(
        f"- #{item['issue_number']}: {item['title']}" for item in input_snapshot
    )
    return (
        "You are a triage assistant. Rank the following open issues by priority "
        "for implementation. Return ONLY a JSON object with a "
        '"ranked_issue_numbers" key containing an array of issue numbers '
        "in priority order (highest first).\n\n"
        f"Issues:\n{issue_lines}\n\n"
        'Example: {"ranked_issue_numbers": [3, 1, 5]}'
    )


def parse_ranking(raw_response: str) -> list[int]:
    """Parse the LLM response into a ranked list of issue numbers.

    Raises TriageError if the response is malformed or missing required keys.
    """
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise TriageError(f"LLM response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TriageError("LLM response is not a JSON object")
    ranked = data.get("ranked_issue_numbers")
    if not isinstance(ranked, list):
        raise TriageError("LLM response missing 'ranked_issue_numbers' array")
    if not all(isinstance(n, int) for n in ranked):
        raise TriageError("'ranked_issue_numbers' must contain only integers")
    return ranked


class TriageClient(Protocol):
    def call(self, config: TriageConfig, prompt: str) -> str:
        """Send the prompt to the LLM and return the raw response text."""
        ...


class HttpTriageClient:
    """OpenAI-compatible chat completions client backed by urllib."""

    def call(self, config: TriageConfig, prompt: str) -> str:
        body = json.dumps({
            "model": config.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }
        headers.update(config.headers)
        req = urllib.request.Request(
            config.endpoint_url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                return str(content)
        except urllib.error.HTTPError as exc:
            raise TriageError(f"LLM HTTP {exc.code}: {exc.reason}") from exc
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise TriageError(f"Malformed LLM response: {exc}") from exc
        except urllib.error.URLError as exc:
            raise TriageError(f"LLM unreachable: {exc.reason}") from exc


def rank_issues(
    conn: sqlite3.Connection,
    project_id: int,
    client: TriageClient,
    sleep: Any = time.sleep,
) -> list[int]:
    """Rank open issues via the triage LLM with retry/backoff (D19, EC-4).

    Returns the ranked issue numbers on success. On failure after MAX_ATTEMPTS,
    persists a failed audit record, creates a notification, and raises TriageError.
    """
    config = load_triage_config(conn)
    input_snapshot = _build_input_snapshot(conn, project_id)
    prompt = build_prompt(input_snapshot)

    last_error: str | None = None
    raw_response: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw_response = client.call(config, prompt)
            ranking = parse_ranking(raw_response)
            # Success — persist audit record.
            repos.triage_run_create_full(
                conn,
                project_id=project_id,
                ranked_issue_ids=ranking,
                input_snapshot=input_snapshot,
                raw_response=raw_response,
                status="success",
                attempts=attempt,
                error=None,
            )
            return ranking
        except (TriageError, Exception) as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("Triage attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc)
            if attempt < MAX_ATTEMPTS:
                sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    # All attempts failed — persist failure record + notification (EC-4, FR-35).
    repos.triage_run_create_full(
        conn,
        project_id=project_id,
        ranked_issue_ids=[],
        input_snapshot=input_snapshot,
        raw_response=raw_response,
        status="failed",
        attempts=MAX_ATTEMPTS,
        error=last_error,
    )
    repos.notification_create(
        conn,
        message=(
            f"Triage failed for project {project_id} after "
            f"{MAX_ATTEMPTS} attempts: {last_error}"
        ),
        project_id=project_id,
    )
    raise TriageError(f"Triage failed after {MAX_ATTEMPTS} attempts: {last_error}")
