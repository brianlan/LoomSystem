import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.console import ConsoleBroker
from app.container_monitor import ContainerMonitor, recover_on_startup
from app.dependencies import DBState
from app.docker import SubprocessDockerAdapter
from app.polling import PollingService, make_adapter_factory
from app.routers import (
    audit,
    console,
    github,
    implementors,
    notifications,
    projects,
    reviewers,
    settings,
    status,
)
from app.trigger import TriggerService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Console broker for live streaming (T14/FR-34).
    broker = ConsoleBroker()
    broker.bind(asyncio.get_running_loop())
    app.state.console_broker = broker

    db_path = app.state.db.db_path
    adapter = getattr(app.state, "docker_adapter", None) or SubprocessDockerAdapter()
    trigger_service = getattr(app.state, "trigger_service", None) or TriggerService(adapter)

    # T13: reconnect to surviving containers, record abandoned triggers, resume schedules.
    with app.state.db.get_connection() as conn:
        recover_on_startup(conn, adapter, trigger_service)
        conn.commit()

    # Background GitHub polling (FR-38). Disabled in tests via app.state.polling_enabled.
    if getattr(app.state, "polling_enabled", True):
        factory = make_adapter_factory(db_path)
        app.state.polling = PollingService(db_path, factory)
        app.state.polling.start()

    # T13: periodic container health monitor. Disabled in tests via app.state.monitoring_enabled.
    if getattr(app.state, "monitoring_enabled", True):
        app.state.container_monitor = ContainerMonitor(db_path, adapter)
        app.state.container_monitor.start()

    yield

    polling_service = getattr(app.state, "polling", None)
    if polling_service is not None:
        polling_service.stop()
    monitor = getattr(app.state, "container_monitor", None)
    if monitor is not None:
        monitor.stop()



app = FastAPI(title="LoomSystem Backend", lifespan=lifespan)
app.state.db = DBState()
app.state.polling_enabled = True
app.include_router(settings.router)
app.include_router(projects.router)
app.include_router(github.router)
app.include_router(reviewers.router)
app.include_router(implementors.router)
app.include_router(console.router)
app.include_router(audit.router)
app.include_router(notifications.router)
app.include_router(status.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
