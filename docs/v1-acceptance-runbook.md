# LoomSystem v1 Acceptance Runbook

This runbook describes how to verify that a LoomSystem build satisfies the v1 acceptance criteria (AC-1 through AC-12) and that every v1 non-goal (NG-1 through NG-14) remains absent. It separates **CI-safe automated checks** from **manual real-environment demonstration steps**.

## Audience

Engineers, reviewers, and operators who need to confirm a LoomSystem deployment is ready for v1.

## Prerequisites

- A local machine with:
  - Python 3.11+ and the backend dependencies installed (`cd backend && pip install -e ".[dev]"`).
  - Node.js 20+ and frontend dependencies installed (`cd frontend && npm install`).
  - Docker daemon running locally.
  - A local SSH key authorized for the target GitHub repo(s).
  - A real GitHub repository with at least a few open issues and pull requests.
- Registered LoomSystem global settings:
  - SSH key.
  - App-level GitHub token with read access to issues and PRs.
  - At least one model entry (provider + model ID + credentials).
  - At least one agent definition (prompt markdown + bound GitHub identity).
  - At least one Docker image reference pointing to an image that contains `opencode`, `gh`, and `git`.
  - Triage LLM configuration (OpenAI-compatible endpoint + model + key).

## Automated absence checks (CI-safe)

The following checks run automatically in CI and do not require real Docker, GitHub, opencode, or model-provider state.

| Non-goal | Check | Location |
|---|---|---|
| NG-1 (app-level auth) | No login / sign-in UI appears on any tab. | `frontend/e2e/non-goals.spec.ts` |
| NG-2 (multi-tenant isolation) | No user-switching / tenant-scoping UI appears. | `frontend/e2e/non-goals.spec.ts` |
| NG-3 (GitHub webhooks) | No webhook setup UI or route exists. | `frontend/e2e/non-goals.spec.ts`, `backend/tests/test_non_goals.py` |
| NG-4 (multi-host orchestration) | No cluster / host-selection UI appears. | `frontend/e2e/non-goals.spec.ts` |
| NG-5 (resource caps) | No CPU / memory / container-quota controls appear. | `frontend/e2e/non-goals.spec.ts` |
| NG-7 (mobile / responsive UI) | UI is desktop-oriented; no mobile-only affordances are asserted. | Manual inspection; responsive breakpoints are not a test target. |
| NG-11 (cloud deployment) | No cloud-provider or managed-service UI appears. | `frontend/e2e/non-goals.spec.ts` |
| NG-13 (programmatic API / SDK) | No SDK download, API-key-for-external-consumers, or swagger-as-product UI appears. | `frontend/e2e/non-goals.spec.ts` |

The remaining non-goals are documented as absent in this runbook rather than automated, because they are either not surfaced in the UI (NG-6, NG-8, NG-9, NG-10, NG-12, NG-14) or are architectural constraints that the codebase already enforces.

## Manual real-environment acceptance walkthrough

Perform these steps against a real GitHub repository. Capture screenshots, logs, or terminal output as evidence.

### AC-1 Global settings registration

1. Open LoomSystem in a browser (http://localhost:5173).
2. Navigate to **Settings**.
3. Register at least one agent definition, one model entry, one Docker image, the SSH key, the app-level GitHub token, and the triage LLM config.
4. Refresh the browser and confirm every value is persisted.

**Expected evidence**: Settings tab shows all registered entries after refresh; backend SQLite file contains the entries.

### AC-2 Project CRUD

1. Navigate to **Projects**.
2. Create a project bound to a real GitHub repo URL.
3. Edit the project and change the reviewer/implementor configuration.
4. Delete the project.

**Expected evidence**: Project appears after creation, updated values persist, and deletion removes it from the list.

### AC-3 Issue/PR listing

1. Create a project bound to a repo with open issues and PRs.
2. Wait up to 1 minute (or trigger a manual refresh if available).

**Expected evidence**: The project view lists the real open issues and PRs; counts match GitHub.

### AC-4 Reviewer launch + console + interval

1. On the **Operator** tab, select the project and click **Launch** under Reviewer.
2. Wait for the reviewer container to reach `running` status.
3. Click the reviewer instance link to open the console.
4. Wait at least 15 minutes (or temporarily lower the trigger interval for the demo).

**Expected evidence**: Console streams opencode output; status shows `1/1 running`; at least two triggers are recorded in the audit trail.

### AC-5 Triage + implementor parallelism

1. On the **Operator** tab, click **Start loop** under Implementor.
2. Wait for triage to rank open issues and launch up to `N` parallel implementors (`N` = project parallelism).

**Expected evidence**: Status shows `Loop: running` and `N/N running`; each implementor is bound to a distinct issue number.

### AC-6 Implementor opens PR, PR merges, issue closes, implementor terminates, refill

1. Wait for an implementor to open a PR (or manually create one with the expected issue reference).
2. Merge the PR on GitHub.
3. Wait for the bound issue to close and for LoomSystem to detect the closure.

**Expected evidence**: Issue status transitions to `Resolved`; the implementor terminates; a new implementor launches for the next triage-ranked issue.

### AC-7 Soft stop + hard kill

1. With implementors running, click **Soft stop**.
2. Confirm existing implementors remain visible and no new ones launch.
3. Click **Hard stop**.

**Expected evidence**: After soft stop, status shows `Loop: draining` and running count does not increase. After hard stop, status shows `Loop: idle`, `0/N running`, and `No implementors.`.

### AC-8 Reviewer termination

1. With a running reviewer, click **Terminate** on the reviewer row.

**Expected evidence**: Reviewer disappears from the list; `docker ps` no longer shows the reviewer container.

### AC-9 Backend restart resilience

1. With a reviewer and at least one implementor running, kill the backend process.
2. Restart the backend.
3. Refresh the browser.

**Expected evidence**: Surviving containers are rediscovered; reviewer and implementor statuses return to `running`; scheduling resumes.

### AC-10 Project deletion cascade

1. Create a project with a running reviewer and running implementors.
2. Delete the project from the **Projects** tab.

**Expected evidence**: Project is removed; all related containers are stopped and removed (`docker ps --filter label=loom.project-id=<id>` returns nothing).

### AC-11 Pre-flight validation

1. Attempt to launch a reviewer with:
   - A non-existent / non-pullable Docker image, **or**
   - Invalid / missing model credentials, **or**
   - An unreachable model provider.

**Expected evidence**: Launch is rejected with a clear error banner before any container is left running; no container is created for the failed launch.

### AC-12 Non-goals honored

Verify each NG item using the checklist below.

## Non-goal absence checklist

| # | Non-goal | How to verify absence |
|---|---|---|
| NG-1 | App-level authentication (login, sessions, RBAC) | Open every tab; confirm there is no login screen, no sign-in button, no password field, and no user/role selector. The UI may show a warning that anyone on the network can access the instance. |
| NG-2 | Multi-tenant isolation | Confirm there is no tenant selector, no user-isolation switch, and no per-user project scoping. |
| NG-3 | GitHub webhook ingestion | Confirm there is no "Add webhook", "Webhook URL", or "GitHub events" UI; `TestClient` requests to `/webhooks/*` return 404. |
| NG-4 | Multi-host / distributed orchestration | Confirm there is no cluster, node, or host selector; containers are launched only against the local Docker daemon. |
| NG-5 | Resource caps / quotas | Confirm there is no CPU, memory, or container-quota input in project or global settings. |
| NG-6 | Concurrent-operator concurrency control | Confirm the UI assumes a single operator; there is no lock indicator, conflict-resolution dialog, or multi-user presence. Documented in the spec as a known limitation. |
| NG-7 | Mobile / responsive UI | Run the app on a desktop browser; verify it is desktop-oriented. Responsive mobile layout is not required or tested. |
| NG-8 | Internationalization | Confirm all visible UI text is English and there is no language selector. |
| NG-9 | Automated scoring of agent output quality | Confirm there is no PR-review score, code-quality grade, or agent-output rating UI. |
| NG-10 | In-container session persistence across container destruction | Confirm sessions are tied to containers; deleting a container and launching a new agent creates a new session. |
| NG-11 | Cloud deployment, external databases, managed services | Confirm there is no cloud-provider setup, external DB connection, or managed-service integration UI. |
| NG-12 | Multi-architecture image support | Confirm there is no CPU-architecture selector in Docker image registration. |
| NG-13 | Programmatic API / SDK for external consumers | Confirm the UI is the only interface; there is no SDK download, external API-key generation, or "build integrations" section. The auto-generated OpenAPI docs are a development aid, not a v1 product surface. |
| NG-14 | Agent-to-agent messaging or coordination beyond indirect-via-repo-state | Confirm agents only coordinate through GitHub repo state; there is no direct agent-to-agent message bus or coordination UI. |

## Running the automated checks

```bash
# Backend
python -m pytest backend/tests/test_non_goals.py -q
python -m pytest backend/tests -q
python -m ruff check backend

# Frontend
cd frontend
npm run typecheck
npm run lint
npm test
npx playwright test
```

## Definition of done for this runbook

- [ ] Runbook covers AC-1 through AC-12 with concrete expected evidence.
- [ ] Runbook covers NG-1 through NG-14 with explicit absence verification notes.
- [ ] Automated absence checks run in CI and pass.
- [ ] Existing backend and frontend tests continue to pass.
- [ ] The runbook does not introduce any out-of-scope feature promise.
