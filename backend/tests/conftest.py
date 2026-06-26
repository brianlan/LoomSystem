"""Shared test fixtures.

Disable the background GitHub polling loop so tests never make real network
calls. Tests exercise the polling logic directly via poll_all/poll_project.
"""

from app.main import app


def pytest_configure(config) -> None:
    app.state.polling_enabled = False
