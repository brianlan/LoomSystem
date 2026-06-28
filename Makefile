.PHONY: backend-install backend-test backend-lint backend-typecheck backend-run frontend-install frontend-test frontend-lint frontend-typecheck frontend-run install test lint typecheck run

backend-install:
	cd backend && pip install -e ".[dev]"

backend-test:
	cd backend && pytest

backend-lint:
	cd backend && ruff check .

backend-typecheck:
	cd backend && mypy app tests

backend-run:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend-install:
	cd frontend && npm install

frontend-test:
	cd frontend && npm test

frontend-lint:
	cd frontend && npm run lint

frontend-typecheck:
	cd frontend && npm run typecheck

frontend-test-e2e:
	cd frontend && npm run test:e2e

frontend-run:
	cd frontend && npm run dev

install: backend-install frontend-install

test: backend-test frontend-test

lint: backend-lint frontend-lint

typecheck: backend-typecheck frontend-typecheck

run: backend-run frontend-run
