import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.console import ConsoleBroker
from app.dependencies import DBState
from app.polling import PollingService, make_adapter_factory
from app.routers import console, github, projects, reviewers, settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Console broker for live streaming (T14/FR-34).
    broker = ConsoleBroker()
    broker.bind(asyncio.get_running_loop())
    app.state.console_broker = broker

    # Background GitHub polling (FR-38). Disabled in tests via app.state.polling_enabled.
    if getattr(app.state, "polling_enabled", True):
        factory = make_adapter_factory(app.state.db.db_path)
        app.state.polling = PollingService(app.state.db.db_path, factory)
        app.state.polling.start()
    yield
    service = getattr(app.state, "polling", None)
    if service is not None:
        service.stop()


app = FastAPI(title="LoomSystem Backend", lifespan=lifespan)
app.state.db = DBState()
app.state.polling_enabled = True
app.include_router(settings.router)
app.include_router(projects.router)
app.include_router(github.router)
app.include_router(reviewers.router)
app.include_router(console.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
