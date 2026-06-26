from fastapi import FastAPI

from app.dependencies import DBState
from app.routers import projects, settings

app = FastAPI(title="LoomSystem Backend")
app.state.db = DBState()
app.include_router(settings.router)
app.include_router(projects.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
