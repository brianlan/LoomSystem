from fastapi import FastAPI

app = FastAPI(title="LoomSystem Backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
