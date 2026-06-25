# LoomSystem v1 — Requirement Specification

**Status**: Ready for implementation
**Date**: 2026-06-25
**Audience**: Engineer (primary implementer)
**Scope**: All features described herein ship together as v1.

---

## 1. Outcomes (Why)

### 1.1 Problem statement

AI coding agents (driven by the `opencode` CLI) are powerful but operationally tedious to run at scale across multiple GitHub projects. An operator who wants one agent continuously reviewing a repo and N parallel agents implementing open issues — across many projects simultaneously — must today hand-manage container lifecycles, session IDs, prompt triggering, GitHub credentials, and live observability per agent. There is no single pane that orchestrates this.

LoomSystem eliminates the hand-management. It is a browser-based control plane that launches, schedules, observes, and terminates opencode-driven agents inside per-agent Docker containers, against many GitHub projects in parallel.

### 1.2 Objectives and success metrics

**Objectives**:
- Orchestrate persistent opencode sessions across many projects in parallel, hidden behind a web UI.
- Provide two agent archetypes — a long-running **reviewer** and a parallelized, issue-bound **implementor** — with distinct lifecycle rules.
- Provide real-time visibility into every agent's opencode session via a per-agent console.
- Enforce domain invariants (per-project reviewer cap, 1 implementor per issue, terminate-on-issue-close).
- Centralize configuration of credentials, models, agent prompts, docker images, and per-project tuning.

**Success metrics (v1 acceptance — end-to-end functional walkthrough)**:
A single operator can, in one browser session:
1. Register global settings (models, agent definitions, ssh-key, docker images, app-level GH token, triage LLM config).
2. Create a project bound to a real GitHub repo.
3. View the project's open issues and PRs (refreshed every 1 min).
4. Launch a reviewer agent (pick model + agent + image), watch its live console, see periodic triggers fire every 15 min.
5. Click "start to implement", watch the triage step rank open issues, watch N parallel implementors launch against the top-N issues.
6. Watch an implementor open a PR; watch the PR merge; watch the implementor's bound issue close on GitHub; verify the implementor terminates and a fresh implementor is launched for the next triage-ranked issue.
7. Click "stop" (soft) and verify no new launches occur; click "kill all" (hard) and verify running implementor containers terminate.
8. Terminate the reviewer; verify its container is removed.
9. Restart the LoomSystem backend mid-run; verify it reconnects to surviving containers and resumes scheduling.

### 1.3 Stakeholders and target users

- **Primary user**: a single operator (developer or team lead) running LoomSystem on their local dev machine.
- **Secondary users (acknowledged limitation)**: a small trusted team may share one instance over a trusted LAN. Multi-operator concurrency is **out of scope for v1** (single-operator assumption; documented as a known limitation).
- **No end-users / external consumers**: LoomSystem is a private operator tool, not a product.

---

## 2. Capabilities (What)

### 2.1 Primary workflows / user journeys

**UJ-1 — First-time setup (global settings)**
Operator opens LoomSystem for the first time and registers the building blocks: a global SSH key (for repo cloning), an app-level GitHub token (for read access), one or more model entries (provider ID + model ID + credentials), one or more agent definitions (name + prompt markdown + bound GitHub identity), one or more docker image refs, an optional outbound network proxy, and a triage LLM config (OpenAI-compatible endpoint + model + key).

**UJ-2 — Create project**
Operator creates a project, gives it a name, and binds it to a GitHub repo URL. The project appears in the dashboard with its current open-issue and open-PR counts. The operator independently configures the project's reviewer (agent + model + image + trigger interval) and implementor (agent + model + image + trigger interval + parallelism N).

**UJ-3 — Launch reviewer**
Operator clicks "launch reviewer". The app launches a dedicated Docker container with the configured image, injects the ssh-key + agent markdown file + model credentials, the container auto-clones the project repo via SSH, and the app fires the first `opencode run` trigger to capture the session-id. The console opens, showing live opencode output. The app schedules subsequent triggers on the configured interval (minimum-gap scheduling).

**UJ-4 — Start implementor loop**
Operator clicks "start to implement". The app runs the LLM-driven triage step against the project's currently-open issues (polled via the app-level GH token), ranks them, and launches up to N parallel implementor containers — each bound to a unique issue from the top of the ranked list. Each implementor's prompt is templated with `{{issue_number}}` and `{{issue_title}}` and is otherwise fixed. Whenever an implementor finishes (its bound issue closes), the app re-runs triage on the remaining open issues and launches a fresh implementor for the top-ranked one — maintaining N concurrent until the open-issue pool is empty.

**UJ-5 — Stop the implementor loop**
Operator clicks "stop" (soft): no new implementor launches; existing implementors continue until their issues close. Operator clicks "kill all" (hard): all running implementor containers terminate immediately.

**UJ-6 — Observe agents**
For every running agent (reviewer and each implementor), the operator can open a per-agent console that replays the full session history on open and then live-streams new output.

**UJ-7 — Restart resilience**
The LoomSystem backend process restarts (crash or reboot). On startup, it rediscovers surviving agent containers (by name/label), reconnects to them, resumes the per-agent trigger schedule (next fire = now + interval), and continues capturing console output. Mid-flight opencode invocations killed by the restart are abandoned.

**UJ-8 — Project deletion**
Operator deletes a project. The app cascade-kills ALL agents running under that project (reviewer + all implementors), removes their containers, and removes the project record.

### 2.2 System responsibilities and boundaries

**LoomSystem is responsible for**:
- Project, agent, model, image, and credential registries (CRUD + validation).
- Per-agent Docker container lifecycle (launch, monitor, restart-with-cap, terminate).
- Per-agent opencode session management (capture session-id on first trigger; reuse on subsequent triggers via `--session <id>`).
- Per-agent periodic trigger scheduling (minimum-gap).
- Per-agent console output capture and live streaming with replay-on-open.
- LLM-driven issue triage (re-rank at every implementor refill).
- GitHub state polling for UI (issues/PRs every 1 min) and for terminators (issue-close detection per implementor).
- Pre-flight validation before any launch (credentials reachable, image present or pullable, model resolvable).
- Restart-time reconnection to surviving containers.

**LoomSystem is NOT responsible for**:
- Authoring opencode, gh, git, or the language toolchains — these are pre-baked into the user-supplied docker image.
- The content/quality of agent prompts (operator authors them in-app).
- The decisions made by agents inside opencode sessions (which PRs to open, what code to write, what to review).
- Git state inside a running container after the initial clone — agents manage their own git operations.
- Multi-operator access control or per-user isolation.

### 2.3 Domain entities + definitions (conceptual)

- **Project**: a named unit bound to exactly one GitHub repo URL. Owns independent reviewer and implementor configurations. Hosts at most R reviewers (R configurable, default 1) and 0–N parallel implementors.
- **Reviewer Agent**: a long-running agent instance bound to a project. Launch = container creation + first opencode session. Periodically triggered with a fixed prompt in the same opencode session. Lifetime ends only on explicit operator termination.
- **Implementor Agent**: an agent instance bound 1:1 to a single project issue. Launch = container creation + first opencode session with the issue context injected via prompt template. Periodically triggered. Lifetime ends when the bound issue is closed on GitHub (any reason: PR-merge, manual close, wontfix).
- **Agent Definition** (registered): a global entry with a name, a markdown prompt (with optional `{{issue_number}}`/`{{issue_title}}` placeholders for implementor use), a bound GitHub identity (account + GH_TOKEN), and opencode permission/mode metadata. Reusable across projects.
- **Model Entry** (registered): a global entry with a provider ID, a model ID (matching the live models.dev catalog), and credentials. Materialized into the container as env vars (built-in providers) or `auth.json`/`opencode.json` (custom providers) at launch.
- **Docker Image** (registered): a global entry naming a docker image (e.g. `my-org/opencode-runtime:latest`). Must pre-bake opencode, gh, and git.
- **Triage LLM Configuration**: a global entry with an OpenAI-compatible endpoint URL, model name, and API key. Used by the app (not by opencode) to rank open issues.
- **App-Level GH Token**: a global GitHub credential used by the LoomSystem backend for all read operations (issue/PR listing, polling). Distinct from per-agent tokens used inside containers.
- **SSH Key**: a single global SSH key used to clone all project repos.
- **Session**: an opencode session (server-generated ID, persisted inside the container). Lives and dies with its container.
- **Trigger**: one execution of `opencode run -m <model> --agent <name> --session <id> "<prompt>"` inside a specific container via `docker exec`. Captures stdout/stderr in real time. Has a start time, end time, exit code, and captured log.
- **Console Capture**: the continuous captured stream of an agent's opencode-session output, retained indefinitely, replayable on console-open.

### 2.4 Assumptions and constraints (explicit)

**Hard constraints (user-mandated)**:
- Browser-based web app; backend runs on the operator's local dev machine; Docker daemon is local.
- Backend stack: **Python** (e.g. FastAPI/Flask — engineer's choice).
- Frontend stack: **React + TypeScript**.
- Data persistence: **embedded SQLite**.
- Orchestration substrate: **opencode CLI**, **gh CLI**, **git**, all pre-baked inside user-supplied docker images.
- Container substrate: **Docker** (local daemon).
- All features described herein ship as v1.

**Validated facts (from opencode source — packages/opencode/src/)**:
- `opencode run -m <provider/model> --agent <name> --session <id> <prompt>` is valid; sessions resume by ID; sessions are stored in SQLite at `$XDG_DATA_HOME/opencode/opencode.db` inside the container.
- `--agent <name>` resolves to a markdown file at `.opencode/agents/<name>.md` (project-local) or `~/.config/opencode/agents/<name>.md` (global). Filename (minus extension) is the agent name. App owns prompt content; injects the file at launch.
- `opencode run --format json` emits NDJSON per event (suitable for live console forwarding). Default mode emits completed text blocks.
- Credentials: env vars (e.g. `ANTHROPIC_API_KEY`) work for built-in providers (autoload=false; env var activates the provider). `~/.local/share/opencode/auth.json` (chmod 600) is the canonical credential store; beats env vars. `opencode.json` registers custom providers. `OPENCODE_AUTH_CONTENT` env var is an in-memory alternative to auth.json.
- Model ID format is always `<provider_id>/<model_id>` and must match the live models.dev catalog. IDs are operator-configured (not hardcoded by the app).
- Missing credentials for Anthropic/OpenAI fail silently: provider absent from `opencode models` output, then `ModelNotFoundError` on first call. App must detect this pre-flight.

**Validated facts (from Docker + gh)**:
- `docker exec` streaming works via subprocess `spawn()` (Python) / `subprocess.Popen` — pipes give live chunks. Must use line-buffered reads; must NOT use buffered `exec()`.
- Containers survive backend restarts; persisting container ID/name is sufficient for re-entry via a fresh `docker exec`. `--restart unless-stopped` survives daemon reboot.
- `gh pr view <N> --json state --jq '.state == "MERGED"'` and `gh issue view <N> --json state --jq '.state == "CLOSED"'` are canonical state probes. Polling at 15s uses ~240 calls/hr, well within the 5,000/hr PAT limit.

**Assumptions**:
- The operator's local machine has Docker installed and the daemon running.
- The operator can pre-authorize the global SSH key against every project repo (deploy key or user key).
- The operator can pre-authorize each registered agent's GitHub identity with the scopes needed for `repo` (read+write) and `workflow` (if PRs touch CI).
- The operator trusts their LAN (no app-level auth).
- The global app-level GH token has read access to every project repo's issues and PRs.

---

## 3. Requirements (Shall)

Each requirement is atomic, traceable to a use case (UJ-#) or decision (D#), and observable.

### 3.1 Functional requirements

#### Projects
- **FR-1** The system shall allow the operator to create, view, update, and delete projects (UJ-2, UJ-8).
- **FR-2** The system shall require each project to be bound to exactly one GitHub repo URL (UJ-2).
- **FR-3** The system shall allow each project to independently configure its reviewer (agent + model + image + trigger interval) and its implementor (agent + model + image + trigger interval + parallelism N + reviewer cap) (UJ-2, D28, D34).
- **FR-4** The system shall default trigger intervals to 15 minutes for both reviewer and implementor (D46).
- **FR-5** The system shall default the reviewer-per-project cap to 1, configurable per project (D28).
- **FR-6** The system shall, upon project deletion, cascade-kill every running agent under that project, remove their containers, and remove the project record (UJ-8, D27).

#### Global registries
- **FR-7** The system shall maintain a global registry of agent definitions. Each entry shall include: name (unique), prompt markdown (editable in-app via a code editor pane), bound GitHub identity (account + GH_TOKEN), and opencode permission/mode metadata (UJ-1, D29, D50).
- **FR-8** The system shall maintain a global registry of model entries. Each entry shall include: provider ID, model ID, optional display name, optional custom-provider config (npm package + baseURL + options), and credentials (API key) (UJ-1, D16).
- **FR-9** The system shall maintain a global registry of docker image references. Each entry shall include: image name (e.g. `repo/img:tag`) (UJ-1).
- **FR-10** The system shall store exactly one global SSH key, used to clone all project repos (UJ-1, D9).
- **FR-11** The system shall store exactly one global app-level GitHub token, used by the backend for all read operations (issue/PR listing, polling) (UJ-1, D36).
- **FR-12** The system shall store exactly one global triage LLM configuration (OpenAI-compatible endpoint URL + model name + API key + optional headers), shared across all projects (UJ-1, D18, D32).
- **FR-13** The system shall store an optional global outbound network proxy, applied to container traffic only (via `HTTP_PROXY`/`HTTPS_PROXY` env vars at `docker run`) (UJ-1, D15).
- **FR-14** The system shall block the deletion of any registered entity (agent, model, image, ssh-key, GH-token, triage config, proxy) that is currently referenced by a project or running agent, surfacing a clear "in use" error (D30).

#### Reviewer agent lifecycle
- **FR-15** The system shall allow the operator to launch up to R reviewer agents per project (R = project's reviewer cap, default 1) by selecting from the global agent, model, and image registries (UJ-3, D28).
- **FR-16** Upon reviewer launch, the system shall: (a) start a Docker container with the selected image, (b) inject the ssh-key, (c) write the agent's markdown file to `.opencode/agents/<name>.md` inside the repo working directory, (d) inject the selected model's credentials via env vars (and `auth.json`/`opencode.json` for custom providers), (e) trigger the container to clone the project repo via SSH, (f) run the first `opencode run -m <model> --agent <name> <prompt>` via `docker exec`, capturing the session-id from the output, and (g) persist the container-id and session-id mapping (UJ-3, D2, D35, F15-F18).
- **FR-17** The system shall trigger each running reviewer on its configured interval using minimum-gap scheduling: no new trigger fires until at least N minutes have elapsed since the previous trigger's start, where N is the configured interval (UJ-3, D26).
- **FR-18** Each reviewer trigger shall re-run the agent's fixed prompt in the same opencode session via `--session <id>` (UJ-3, D5).
- **FR-19** The system shall allow the operator to manually fire a trigger on-demand for a running reviewer (D38).
- **FR-20** The system shall allow the operator to terminate a running reviewer; on termination, the system shall stop and remove its container (UJ-3).

#### Implementor agent lifecycle
- **FR-21** The system shall allow the operator to start the implementor loop with a single "start to implement" action per project (UJ-4).
- **FR-22** Upon "start to implement", and upon every implementor refill (when a running implementor finishes), the system shall run the LLM-driven triage step using the configured triage LLM (OpenAI-compatible API) against the project's currently-open issues (UJ-4, D11, D17, D18).
- **FR-23** The triage step shall rank all currently-open issues for the project; the system shall launch up to N fresh implementors for the top-N ranked issues (where N = project's configured parallelism), maintaining N concurrent implementors until the open-issue pool is empty (UJ-4, D10, D21).
- **FR-24** Each implementor shall be bound 1:1 to a unique issue; the system shall not assign the same issue to two implementors (UJ-4).
- **FR-25** Each implementor launch shall follow the same container-creation + credential-injection + repo-clone + first-trigger sequence as FR-16, except: the agent's prompt shall be templated with `{{issue_number}}` and `{{issue_title}}` replaced by the bound issue's number and title (UJ-4, D24).
- **FR-26** The system shall trigger each implementor on its configured interval using minimum-gap scheduling (D26).
- **FR-27** The system shall poll the bound issue's state via `gh issue view <N> --json state` on a cadence sufficient to detect closure within ~15 seconds (F8). When the issue is closed (state = CLOSED, any reason), the system shall terminate the implementor (stop and remove its container) and immediately trigger a refill (FR-23) (UJ-4, D6).
- **FR-28** The system shall instruct the implementor agent (via its prompt) to include "Closes #<issue-number>" in any PR it opens; the system shall verify the linkage post-open and amend the PR description if missing (D37).
- **FR-29** When all open issues for a project are resolved AND no implementor is running, the system shall stop launching new implementors and surface a "backlog drained" notification (UJ-4, D39).
- **FR-30** The system shall provide two distinct stop actions for the implementor loop: (a) "stop" (soft) — no new implementor launches; running implementors continue until their issues close; (b) "kill all" (hard) — all running implementor containers terminate immediately (UJ-5, D23).
- **FR-31** When an implementor fails permanently (D8 retry cap hit on container auto-restart), the system shall re-queue its bound issue into the eligible pool for the next triage cycle (D42).

#### Console & observability
- **FR-32** The system shall provide one live console per running agent (reviewer and each implementor), accessible from the project view (UJ-6, D12).
- **FR-33** The system shall continuously capture each agent's stdout/stderr from its opencode session and persist the capture indefinitely (UJ-6, D13, D49).
- **FR-34** When the operator opens a console, the system shall replay the full captured history, then live-stream new output (UJ-6, D13).
- **FR-35** The system shall surface in-app banner/toast notifications for: agent failures (permanent), PRs opened, issues resolved, triage failures (after D19 retry cap), and "backlog drained" events (D39).

#### Restart resilience
- **FR-36** On LoomSystem backend restart, the system shall rediscover surviving agent containers (by deterministic name or label), reconnect to them, resume each agent's trigger schedule (next fire = restart-time + interval), and continue capturing console output (UJ-7, D31).
- **FR-37** Mid-flight opencode invocations killed by a backend restart shall be abandoned; no attempt to resume the in-flight call shall be made (D31).

#### GitHub polling & UI
- **FR-38** The system shall poll GitHub via the app-level GH token once per minute per project to refresh the project's open-issue and open-PR lists shown in the UI (D47).
- **FR-39** The system shall display, per project: open issues, open PRs, running reviewer status, per-implementor status (assigned issue, container status, last-trigger timestamp, PR-opened indicator), and per-issue status (unassigned, in-progress, PR-opened, resolved, failed) (UJ-2, UJ-6).

#### Settings change semantics
- **FR-40** Changes to cadence-type settings (trigger intervals, polling cadence, reviewer cap) shall apply immediately to running agents (D25).
- **FR-41** Changes to identity/infra-type settings (agent prompt, model credentials, docker image, ssh-key, GH tokens) shall apply only to agents launched AFTER the change; running agents shall retain their launch-time configuration (D25).

### 3.2 Non-functional requirements

#### Performance
- **NFR-1** Console live-stream latency from `docker exec` stdout to browser shall be ≤ 2 seconds under normal output volume.
- **NFR-2** Console replay-on-open of a long session (≥10 MB capture) shall begin rendering within 3 seconds.
- **NFR-3** UI page transitions shall complete in ≤ 1 second on local localhost.

#### Reliability
- **NFR-4** The system shall auto-restart any agent container that dies unexpectedly, with a per-agent retry cap (configurable globally, default 5 retries within 1 hour); after the cap is hit, the agent shall be marked "failed" and surfaced in the UI (D8).
- **NFR-5** The system shall not lose captured console data across backend restarts; the SQLite-backed capture store shall be durable (D31, D49).

#### Security & privacy
- **NFR-6** The system shall store all credentials (GH tokens, ssh-key, model API keys, triage LLM key) at rest in the SQLite database. The database file shall be readable only by the operating-system user running LoomSystem (filesystem permissions 0600 on the DB file).
- **NFR-7** The system shall inject credentials into containers only via env vars at `docker run` time or via `auth.json` written with filesystem mode 0600; credentials shall never appear in container logs or in `docker inspect` output beyond the env-var block.
- **NFR-8** The system shall NOT provide app-level authentication (v1 non-goal); the operator assumes a trusted LAN. This shall be documented in the operator-facing UI ("Anyone on this network can access this instance and its credentials").

#### Cost
- **NFR-9** The system shall not enforce resource caps (CPU, memory, container count) per D14; the operator trusts the host. **This is an explicit non-goal documented in the UI.**

### 3.3 Data & domain definitions (conceptual)

(See §2.3 for entity definitions. Schemas are implementation-detail and not prescribed.)

The following state machines are observable system behavior:

**Issue state (from LoomSystem's perspective)**:
- `Unassigned` → `In-Progress` (implementor launched and bound) → `PR-Opened` (implementor opened a PR) → `Resolved` (issue closed on GitHub) OR `Failed` (implementor hit retry cap; issue re-queued back to `Unassigned` per FR-31).
- `Resolved` issues may transition back to `Unassigned` if reopened on GitHub (FR-D45).

**Reviewer state**: `NotRunning` → `Running` (launch) → `NotRunning` (operator termination OR project deletion OR entity-deletion cascade).

**Implementor state**: `NotRunning` → `Running` (auto-launched) → `NotRunning` (issue closed OR kill-all OR project deletion OR permanent failure).

**Implementor loop state per project**: `Idle` → `Running` (start to implement) → `Draining` (soft stop) → `Idle` (last implementor finishes). Hard kill bypasses `Draining`.

### 3.4 Observability & auditability

- **OBS-1** The system shall record, per agent, a complete audit trail: launch event (config snapshot, container-id, session-id), every trigger event (start time, end time, exit code, captured stdout/stderr reference), every state transition, PR-opened event, issue-close event, and termination event.
- **OBS-2** The system shall record every triage step: input issue list snapshot, LLM response, ranked output, and any retry/failure events.
- **OBS-3** The system shall record every container lifecycle event: create, start, die, restart-attempt, restart-cap-hit, remove.
- **OBS-4** The audit trail shall be retained indefinitely (D49) and be browsable from the UI on a per-agent basis.
- **OBS-5** The system shall surface aggregate status (running counts, recent failures, backlog size) on a top-level dashboard.

### 3.5 Operations & lifecycle

- **OPS-1** Deployment model: operator runs LoomSystem on their local machine. No cloud dependency. No external databases. SQLite file lives on the local filesystem.
- **OPS-2** First-run: empty database; operator walked through UJ-1 (global settings registration) before any project can be created.
- **OPS-3** Upgrade: schema migrations are the engineer's responsibility; the spec does not prescribe a migration framework. v1 → v2 migration is out of scope.
- **OPS-4** Backup: the SQLite file is the single source of truth; operator backs it up via filesystem copy. No in-app backup feature in v1.
- **OPS-5** Uninstall: stopping the backend and deleting the SQLite file removes all LoomSystem state. Surviving Docker containers must be manually removed (`docker ps --filter label=loom=true --rm`).

### 3.6 Edge cases & failure handling

- **EC-1** **Container dies mid-trigger** (OOM, crash): the system records the incomplete trigger (exit code ≠ 0 or lost stdout), increments the agent's restart counter, auto-restarts the container (per NFR-4), and resumes on the next interval. The opencode session persists inside the (now-restarted) container only if the container's filesystem survived; otherwise a fresh session is created and recorded.
- **EC-2** **Backend dies mid-trigger**: the in-flight `docker exec` is killed; on backend restart (FR-36), the system reconnects to the surviving container and resumes scheduling. The abandoned trigger is recorded as incomplete.
- **EC-3** **`opencode run` exits non-zero**: recorded as a failed trigger (not a container failure; does not increment the restart counter). Next interval fires normally.
- **EC-4** **Triage LLM call fails**: the system retries up to 10 times (with exponential backoff). If all 10 attempts fail, the system hard-fails the refill attempt (no new implementor launched for this cycle), surfaces a "triage failed" notification, and retries at the next refill cycle (when another implementor finishes) (D19).
- **EC-5** **App-level GH token revoked or rate-limited**: UI polling fails; system surfaces a clear "app-level GH token invalid" banner; agent triggering continues (agents use their own per-agent tokens, not the app-level one).
- **EC-6** **Per-agent GH token revoked**: in-container git push / gh calls fail; the agent's trigger exits non-zero (EC-3 applies). System does not specifically detect token revocation — surfaces as repeated trigger failures.
- **EC-7** **Docker image not present locally at launch**: the system runs `docker pull <image>` automatically; if pull fails (registry unreachable, auth missing), the launch is rejected with a clear error (D43).
- **EC-8** **SSH key rejected by GitHub**: repo clone fails at container startup; system retries per NFR-4; after retry cap, agent is marked failed (D8).
- **EC-9** **Model credential missing or invalid**: pre-flight validation (FR-Run pre-flight) detects via "provider absent from `opencode models` output" or by issuing a minimal LLM call; launch is rejected with a clear "credential missing for `<provider>`" error (D48, F19).
- **EC-10** **Issue closed without a PR** (e.g. wontfix): per D6, the implementor terminates normally. The implementor's work-in-progress branches may remain on the repo; the system does NOT clean them up.
- **EC-11** **Issue reopened after close**: per D45, the issue re-enters the triage pool at the next cycle.
- **EC-12** **Concurrent operator actions** (two team members on the shared instance): undefined behavior; documented as a known limitation. Last-write-wins for state mutations (D40).
- **EC-13** **Operator attempts to delete an in-use entity**: blocked with a clear "in use" error (D30, FR-14).
- **EC-14** **`gh` polling detects the bound PR was closed WITHOUT merge** (e.g. closed by another operator): if the issue is still open, the implementor continues; if the issue auto-closed via "Closes #N" linkage but the PR was closed-without-merge, the implementor terminates (issue is closed).

### 3.7 Non-goals (explicitly out of scope for v1)

- **NG-1** App-level authentication (login, sessions, RBAC).
- **NG-2** Multi-tenant isolation (per-user scoping).
- **NG-3** GitHub webhook ingestion (real-time event push). All GitHub state changes are polling-derived.
- **NG-4** Multi-host / distributed container orchestration (k8s, Swarm). Single local Docker daemon only.
- **NG-5** Resource caps / quotas (CPU, memory, container count). The operator trusts the host.
- **NG-6** Concurrent-operator concurrency control (locking, conflict resolution).
- **NG-7** Mobile / responsive UI. Desktop browser only.
- **NG-8** Internationalization (i18n). English-only UI text.
- **NG-9** Automated testing of agent output quality (e.g. PR review quality scoring).
- **NG-10** In-container session persistence across container destruction (sessions live and die with their containers per D3).
- **NG-11** Cloud deployment, external databases, managed services.
- **NG-12** Multi-architecture image support (arm64/amd64 selection).
- **NG-13** Programmatic API / SDK for external consumers (UI is the only interface).
- **NG-14** Agent-to-agent messaging or coordination beyond indirect-via-repo-state.

---

## 4. Acceptance criteria

**v1 is "done" when the end-to-end functional walkthrough in §1.2 succeeds on a real GitHub repo**, with all 9 enumerated steps verifiable by manual demonstration. Specifically:

| Criterion | Verification |
|---|---|
| AC-1 Global settings registration works | Operator can register: 1+ model, 1+ agent, 1+ image, ssh-key, app-level GH token, triage LLM config — all persisted across restarts. |
| AC-2 Project CRUD works | Operator can create, view, update, delete a project bound to a real repo. |
| AC-3 Issue/PR listing works | UI shows real open issues + PRs for the bound repo, refreshing within 1 minute of upstream changes. |
| AC-4 Reviewer launch + console + interval | Reviewer launches, container runs, console streams live, ≥2 triggers fire at 15-min minimum-gap. |
| AC-5 Triage + implementor parallelism | "Start to implement" triggers triage; N parallel implementors launch against the top-N triaged issues; console per implementor works. |
| AC-6 Implementor opens PR + PR merges + issue closes + implementor terminates | Demonstrated on one implementor end-to-end; refill launches next implementor. |
| AC-7 Soft stop + hard kill | Soft stop halts new launches; hard kill terminates running implementors. |
| AC-8 Reviewer termination | Operator terminates reviewer; container removed. |
| AC-9 Backend restart resilience | Backend killed mid-run; on restart, reconnects to surviving containers, resumes scheduling. |
| AC-10 Project deletion cascade | Delete a project with running agents; all its containers are removed. |
| AC-11 Pre-flight validation | Launching with invalid credentials / missing image / unreachable model is rejected with a clear error before any container starts. |
| AC-12 Non-goals honored | All NG-* items verified absent (no login screen, no webhooks, no resource caps UI, etc.). |

---

## 5. Decision log

Each decision records the choice and rationale. Decisions are referenced from requirements as `D#`.

| # | Decision | Rationale |
|---|---|---|
| D1 | Docker image is fully pre-baked (opencode + gh + git + toolchains). | Simplifies app contract; operator owns image maintenance. |
| D2 | App owns agent prompt content; injects the markdown file at container launch. | opencode's `--agent` resolves by name to a file in `.opencode/agents/`; app must materialize it (F4). |
| D3 | Session = container lifetime (no DB volume mount). | Simpler; acceptable because one container hosts the session for the agent's whole lifetime. |
| D4 | Repo clone transport = SSH + ssh-key. | Standard for developers; key doesn't expire. |
| D5 | Reviewer trigger = re-run fixed prompt in same session. | Simple, deterministic; agent sees its own prior context. |
| D6 | Implementor terminator = issue-closed (NOT PR-merge). | Looser than original wording; covers wontfix/closed-without-PR. **Supersedes original "only after PR merged".** |
| D7 | GitHub identity = per-agent. | Each agent acts as its own GitHub account for commit/PR attribution. |
| D8 | Container failure = auto-restart with retry cap (default 5/hr). | Bounded self-healing without runaway. |
| D9 | SSH key scope = global. | Simplifies setup; one key pre-authorized against all repos. |
| D10 | Implementor concurrency = continuous refill until empty. | Matches "when all open issues are resolved, no implementor will be launched". |
| D11 | Issue selection = LLM-driven priority triage. | Newly identified feature; operator wanted LLM-based judgment. |
| D12 | Every agent (reviewer + each implementor) gets a console. | Symmetric observability. |
| D13 | Console = capture + replay on open. | Lets operator see what they missed. |
| D14 | Resource caps = none (trust the host). | Operator's machine, operator's responsibility. |
| D15 | Network proxy = outbound only, container traffic. | Matches original "network proxy for docker container". |
| D16 | Model registry = rich entries (provider + credentials). | Required because the app must materialize credentials into containers. |
| D17 | Triage cadence = re-rank at every refill. | Most adaptive; cost accepted per D14. |
| D18 | Triage config = app-side global. | Decouples triage from per-project opencode config. |
| D19 | Triage failure = retry 10x then hard-fail. | Bounded retry; surfaces error visibly. |
| D20 | Agent prompt editability = fixed per agent (with D24 exception). | Reproducibility across triggers. |
| D24 | Implementor prompt is templated (`{{issue_number}}`, `{{issue_title}}` only). | Carries issue identity into the container without making the prompt fully dynamic. |
| D25 | Settings changes: cadences live, identity/infra pinned. | Cheap updates apply immediately; expensive ones only on relaunch. |
| D26 | Trigger overlap = minimum-gap scheduling. | Prevents prompt pile-up; bounds frequency naturally. |
| D27 | Project deletion = cascade-kill and delete. | Strong cleanup guarantee. |
| D28 | Reviewer cap = configurable (default 1). | Allows multi-angle review in future; default matches original description. |
| D29 | Agent registry = global. | Simpler; any project can use any registered agent. |
| D30 | Deletion of in-use entities = blocked. | Prevents orphaned references. |
| D31 | App restart = reconnect + resume scheduling. | Robust to backend crashes; containers do the heavy lifting. |
| D32 | Triage LLM protocol = OpenAI-compatible. | Most general; covers OpenAI, OpenRouter, vLLM, Ollama, etc. |
| D33 | Per-project implementor config = one shared (agent, model, image) triple. | Simpler; per-issue routing deferred to future versions. |
| D34 | Project-level: reviewer and implementor configured independently. | Different roles warrant different tooling/model choices. |
| D35 | Credential injection = env vars (built-in providers) + auth.json (canonical/override) + opencode.json (custom providers). | Verified from opencode source (F15-F18). |
| D36 | App-level GH token = global. | App needs its own read credential independent of per-agent tokens. |
| D37 | PR↔issue linking = prompt instructs agent; app verifies. | Lightweight; falls back to amend if missing. |
| D38 | Manual controls = manual trigger only (no pause/resume). | Pause-without-terminate adds complexity for marginal value in v1. |
| D39 | Notifications = in-app only. | Simplest delivery; operator monitors UI. |
| D40 | Concurrent operator actions = out of scope (single-operator assumption). | Documented limitation; trusted-team scenario. |
| D41 | PR base branch = auto-detect repo default. | No setting needed. |
| D42 | Failed implementor's issue = re-queue automatically. | Self-healing; bounded by issue-close as the only true terminator. |
| D43 | Docker image = auto-pull if missing. | Smoother first launch. |
| D44 | Repo state in container = agent-managed. | App never touches post-clone; predictable. |
| D45 | Reopened issue = re-enter triage pool. | Matches GitHub lifecycle naturally. |
| D46 | Default trigger intervals = both 15 min. | Aggressive but matches original example. |
| D47 | UI issue/PR polling = 1 min. | Fresh enough for monitoring without burning quota. |
| D48 | Validation strictness = validate at launch. | Balanced UX; errors surface pre-launch. |
| D49 | Audit log retention = forever. | Operator manually prunes if needed. |
| D50 | In-app prompt editing = in scope (full editor pane). | Convenient authoring UX. |
| D51 | Acceptance criteria = end-to-end functional walkthrough. | Sufficient for v1 demo; per-requirement procedures deferred. |
| D52 | Non-goals list confirmed. | Locks scope. |

---

## 6. Open questions

**Blocking**: none. All v1-blocking decisions are resolved.

**Non-blocking** (deferred to engineer's judgment, listed for transparency):
- Q-1 Exact JSON shape of `auth.json` for OAuth providers (only relevant if operator registers an OAuth-only provider; v1 likely uses API-key-only auth).
- Q-2 Triage prompt text + output-format contract (engineer designs; not user-facing).
- Q-3 Container naming/label scheme (engineer decides; suggested format: `loom-<project-id>-<role>-<random>`).
- Q-4 Default per-agent retry cap window (suggested default: 5 retries within 1 hour; operator-configurable globally).
- Q-5 DB schema, migrations approach, and exact retention storage format (engineer decides).
- Q-6 Console transport (WebSocket vs SSE) and rendering format (raw text vs ANSI-rendered vs NDJSON-rendered).
- Q-7 Whether to detect "credential missing" via `opencode models` output parsing or a minimal pre-flight LLM call (engineer decides based on opencode CLI stability).
- Q-8 Per-project setting for "issue eligibility filter" (e.g. exclude `epic`/`blocked` labels). Currently defaulted to "all open issues eligible"; may add filter in v1.1 if operator requests.

---

## 7. Traceability

Maps requirements → objectives (§1.2) and use cases (§2.1).

| Requirement | Primary use case | Objective supported |
|---|---|---|
| FR-1 – FR-6 (Projects) | UJ-2, UJ-8 | Multi-project orchestration |
| FR-7 – FR-14 (Registries) | UJ-1 | Centralized configuration |
| FR-15 – FR-20 (Reviewer) | UJ-3 | Reviewer archetype |
| FR-21 – FR-31 (Implementor) | UJ-4, UJ-5 | Implementor archetype + parallelism + triage |
| FR-32 – FR-35 (Console) | UJ-6 | Real-time visibility |
| FR-36 – FR-37 (Restart) | UJ-7 | Reliability |
| FR-38 – FR-39 (UI) | UJ-2, UJ-6 | Multi-project monitoring |
| FR-40 – FR-41 (Settings) | UJ-1, UJ-2 | Centralized configuration |
| NFR-4 | UJ-7 | Reliability |
| NFR-6, NFR-7 | (cross-cutting) | Security baseline |
| OBS-1 – OBS-5 | UJ-6 | Observability |
| EC-1 – EC-14 | (cross-cutting) | Reliability |

**Conflicts / tensions documented** (not blocking):
- D40 (single-operator assumption) vs Phase 1 answer "small team, shared instance": documented in NG-6 and §1.3 as a known limitation.
- NFR-9 (no resource caps) implies operator discipline; documented in the UI per NFR-9.

---

**End of specification.**
