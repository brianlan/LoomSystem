# LoomSystem

A browser-based local control plane for orchestrating opencode-driven agents across GitHub projects.

## Local Development

### Prerequisites

- Python 3.11+
- Node.js 20+
- npm

### Install Dependencies

```bash
make install
```

Or manually:

```bash
cd backend && pip install -e ".[dev]"
cd ../frontend && npm install
```

### Run Locally

Backend:

```bash
make backend-run
```

Frontend (in another terminal):

```bash
make frontend-run
```

The backend API is available at `http://localhost:8000` and the frontend at `http://localhost:5173`.

### Test, Lint, and Typecheck

Run everything:

```bash
make test
make lint
make typecheck
```

Backend only:

```bash
make backend-test
make backend-lint
make backend-typecheck
```

Frontend only:

```bash
make frontend-test
make frontend-lint
make frontend-typecheck
```

## Project Structure

- `backend/` — Python/FastAPI backend skeleton
- `frontend/` — React + TypeScript frontend skeleton
- `docs/` — Requirement specifications
