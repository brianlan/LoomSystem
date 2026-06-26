import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import get_db
from app.dependencies import DBState
from app.polling import PollingScheduler
from app.routers import github, projects, settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    db_state: DBState = app.state.db
    # ponytail: env-gate so tests don't spawn a background poller.
    if os.environ.get("LOOMSYSTEM_DISABLE_POLLING"):
        app.state.polling_scheduler = None
        yield
        return
    scheduler = PollingScheduler(get_db(db_state.db_path))
    app.state.polling_scheduler = scheduler
    scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="LoomSystem Backend", lifespan=lifespan)
app.state.db = DBState()
app.include_router(settings.router)
app.include_router(projects.router)
app.include_router(github.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
