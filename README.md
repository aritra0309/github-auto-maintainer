# Github Auto-Maintainer

Automated GitHub maintenance agent that reviews pull requests, responds to issue comments, and helps keep repositories healthy through policy-driven checks.

## Completed So Far (Phase 1)

Phase 1 is complete: deterministic, policy-driven model routing is implemented and covered by tests.

### Deliverables completed

- Created `src/github_auto_maintainer/core/settings.py` using `pydantic-settings`.
- Created `src/github_auto_maintainer/core/model_catalog.py` as a typed loader/validator for `config/models.yaml`.
- Created `src/github_auto_maintainer/core/task_types.py` with `TaskType` and `TaskComplexity`.
- Created `src/github_auto_maintainer/core/routing_policy.py` for deterministic rank/select/escalation logic.
- Updated `src/github_auto_maintainer/core/llm_router.py` with `complete_task(...)` and `complete_with_escalation(...)`.
- Updated `.env.example` and runtime defaults to `DEFAULT_PROVIDER=openai` and `DEFAULT_MODEL=gpt-5.4-mini`.

### Architecture decisions implemented

- `LLMRouter` remains the execution engine; routing policy is isolated in a separate module.
- Routing is deterministic (no LLM in the routing decision path).
- Escalation order is fixed: `low -> medium -> high`.

### Testing completed

- Unit tests for model catalog parsing and validation failures.
- Unit tests for deterministic task-to-model mapping.
- Escalation tests with fake validators and fake providers.
- Existing router tests preserved and expanded.

### Current runtime scope

- Current entrypoint is bootstrap-oriented for local validation.
- Webhook ingress and GitHub App runtime orchestration are not implemented yet.

## Future Work Roadmap

### Phase 2 - Webhook ingress + GitHub App auth + queue

Effort: 5-7 days  
Goal: Safely receive real GitHub events and normalize them into internal jobs.

Deliverables:

- Create `src/github_auto_maintainer/server/app.py` (FastAPI app, `/webhook`, `/health`).
- Create `src/github_auto_maintainer/server/webhooks.py` (signature verification, header parsing).
- Create `src/github_auto_maintainer/github/auth.py` (JWT + installation token).
- Create `src/github_auto_maintainer/github/events.py` (normalized event envelope).
- Create `src/github_auto_maintainer/core/job_queue.py` (in-memory async queue abstraction).
- Add dependencies in `pyproject.toml`: `fastapi`, `uvicorn`, JWT library.

Architecture decisions:

- Webhook handler does validate + enqueue only (no LLM call inline).
- Normalize to internal event names (`pull_request.opened`, etc.) before orchestration.
- Keep queue interface swappable (Redis/Celery later).

Testing:

- Signature verification unit tests (valid/invalid/missing).
- Token generation tests with fixture key.
- Integration test: webhook POST -> queue contains normalized job.

Definition of done:

- Local server receives GitHub webhooks through ngrok/smee.
- Invalid signatures are rejected.
- Installation token retrieval works for a target repo installation.

### Phase 3 - GitHub client + diff parser + read-only skills

Effort: 6-9 days  
Goal: Build the read + reason loop with no writes yet (low risk, high signal).

Deliverables:

- Create `src/github_auto_maintainer/github/client.py` with async `httpx` methods:
  - `get_pull_request(...)`
  - `get_pull_request_diff(...)`
  - `get_pull_request_files(...)`
  - `get_issue(...)`
  - `get_issue_comments(...)`
- Create `src/github_auto_maintainer/github/diff_parser.py` with `FileDiff`, `DiffHunk`, `parse_diff(...)`.
- Create `src/github_auto_maintainer/skills/base.py` (`SkillContext`, `SkillResult`, `BaseSkill`).
- Create read-only skills:
  - `src/github_auto_maintainer/skills/pr_triage.py`
  - `src/github_auto_maintainer/skills/issue_triage.py`
- Add prompt templates:
  - `src/github_auto_maintainer/prompts/pr_triage.md`
  - `src/github_auto_maintainer/prompts/issue_triage.md`

Architecture decisions:

- Keep output schema strict and typed (no raw prose parsing).
- Use REST only in v1 (no GraphQL yet).
- Keep first result as log/JSON action recommendation; no GitHub write.

Testing:

- `respx`-mocked tests for each GitHub endpoint call.
- Parser tests for multi-file/multi-hunk diffs.
- Golden tests for skill output schema correctness.
- Routing tests: triage defaults to low tier unless escalated.

Definition of done:

- PR/issue events produce typed triage decisions from real payload fixtures.
- No mutation to GitHub yet.
- Error mapping handles rate-limit/transient vs non-retryable.

### Phase 4 - Orchestrator + first write actions (idempotent)

Effort: 7-10 days  
Goal: Deliver visible GitHub value safely: summary comments, labels, review summaries.

Deliverables:

- Create `src/github_auto_maintainer/core/orchestrator.py`.
- Create `src/github_auto_maintainer/core/idempotency.py` (delivery dedupe).
- Create `src/github_auto_maintainer/core/action_policy.py`.
- Extend `src/github_auto_maintainer/github/client.py` with write methods:
  - `create_issue_comment(...)`
  - `add_labels(...)`
  - `create_pr_review_summary(...)` (summary-level review first, not line comments).
- Add write-capable skills:
  - `src/github_auto_maintainer/skills/pr_summary.py`
  - `src/github_auto_maintainer/skills/issue_label.py`
  - `src/github_auto_maintainer/skills/issue_response.py`
- Update `src/github_auto_maintainer/__main__.py` to run server + worker loop.

Architecture decisions:

- Idempotency key = GitHub delivery ID + action fingerprint.
- Add `DRY_RUN` mode as a mandatory feature flag.
- Add allowlist: repo + event types.

Testing:

- Duplicate webhook replay test (must not duplicate comments).
- Integration tests for write calls with mocked API.
- Permission/allowlist tests.

Definition of done:

- PR and issue events trigger real comments/labels in a sandbox repo.
- Duplicate deliveries are safe.
- Dry-run can simulate full flow without writes.

### Phase 5 - Controlled auto-fix pipeline (branch/commit/PR)

Effort: 10-14 days  
Goal: Create safe code-fix PRs with hard guardrails and human approval.

Deliverables:

- Create `src/github_auto_maintainer/automation/patch_worker.py`.
- Create `src/github_auto_maintainer/automation/git_ops.py`.
- Create `src/github_auto_maintainer/automation/safety.py` (path/diff/size rules).
- Implement workflow: fetch request -> generate patch -> apply -> run checks -> commit -> open PR.
- Persist run metadata (SQLite is acceptable for v1): `src/github_auto_maintainer/core/run_store.py`.

Architecture decisions:

- No arbitrary command execution from model output.
- Allowed command templates only (`ruff`, `mypy`, `pytest`).
- Block sensitive paths by default (`.github/workflows`, secrets files, lockfiles optional by policy).

Testing:

- End-to-end tests on temporary git repositories.
- Failure-mode tests: patch conflict, test fail, lint fail.
- Security tests: forbidden path edits, oversized diffs.

Definition of done:

- Bot can open a safe, small fix PR from an issue request.
- Unsafe requests are rejected with explicit reason.
- Human remains required for merge.

### Phase 6 - Deployment + observability + portfolio polish

Effort: 5-8 days  
Goal: Make it production-runnable and portfolio-strong.

Deliverables:

- Add `Dockerfile` and optional `docker-compose.yml`.
- Add deployment docs for:
  - always-on webhook mode (Fly/Railway/Render)
  - GitHub Actions-driven mode
- Add runbook docs:
  - `docs/deploy.md`
  - `docs/security.md`
  - `docs/ops.md`
- Add metrics/logging fields (`event_id`, `delivery_id`, `selected_model`, `escalation_count`, `latency`).

Architecture decisions:

- Support both modes; webhook mode is primary, Actions mode is fallback.
- Keep storage simple (SQLite) until throughput requires upgrade.

Testing:

- Container smoke test.
- Staging repo soak test (multi-day).
- Chaos-lite: duplicate events, provider outage, API 429.

Definition of done:

- Reproducible demo from webhook -> decision -> action.
- Deployment documented and repeatable by a new user.
- Portfolio artifacts ready (GIF, architecture diagram, before/after examples).

## AI-Safe Execution Contract

```yaml
project_rules:
  language: "python>=3.12"
  style: "ruff + mypy strict + pytest"
  async_required: true
  use_existing_components:
    - "core/llm_router.py"
    - "providers/*"
    - "core/hooks.py"
    - "config/models.yaml"
  forbidden_for_v1:
    - "GraphQL client"
    - "multi-agent orchestration"
    - "autonomous merge"
    - "UI dashboard"

execution_policy:
  - "Implement exactly one phase at a time."
  - "Only create/update files listed for the active phase."
  - "Do not invent APIs; use documented GitHub REST endpoints only."
  - "If endpoint uncertainty exists, add TODO + failing test rather than guessing."
  - "Write/extend tests before finalizing implementation."
  - "Return a phase report: changed files, tests added, commands run, remaining risks."

quality_gates:
  commands:
    - "python -m ruff check ."
    - "python -m mypy ."
    - "python -m pytest"
  must_pass: true

safety_gates:
  - "Webhook signature verification required before processing."
  - "Idempotency required before GitHub write actions."
  - "DRY_RUN mode must exist before enabling writes."
  - "No secrets in logs."
```

## Run Locally

1. Create and activate a Python environment.
2. Install dependencies:

```bash
python -m pip install -e '.[dev]'
```

3. Copy the env template and fill values:

```bash
cp .env.example .env
```

Default router settings in `.env.example`:

- `DEFAULT_PROVIDER=openai`
- `DEFAULT_MODEL=gpt-5.4-mini`
- `MODEL_CATALOG_PATH` optional override (defaults to `config/models.yaml`)

4. Run tests and checks:

```bash
make lint
make test
```

5. Run the bootstrap CLI:

```bash
make run
```

For offline local development with Ollama:

```bash
make run-local
```

## Foundation Layout

- `src/github_auto_maintainer/core/` core runtime modules.
- `src/github_auto_maintainer/skills/` skill definitions and orchestration.
- `src/github_auto_maintainer/tools/` tool adapters and helpers.
- `src/github_auto_maintainer/providers/` one module per provider backend.
- `config/models.yaml` provider/model catalog for router decisions.
- `tests/` test suite.

## Add A New Provider

1. Implement a provider class under `src/github_auto_maintainer/providers/`.
2. Match the provider interface used by the dispatcher.
3. Register the provider in the provider registry.
4. Add provider-specific env vars in `.env.example`.
5. Add tests for success and failure paths.
