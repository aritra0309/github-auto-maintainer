# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

GitHub Auto-Maintainer is a policy-driven automation system for GitHub repository maintenance (triage, routing, summarization, follow-up). The core focus is deterministic, auditable model routing—not raw LLM generation quality.

Design goals:
- Observe GitHub events safely.
- Normalize them into typed internal objects.
- Choose models deterministically.
- Validate before acting.
- Keep future write actions idempotent and test-backed.

## Project Status

### Completed

**Phase 1 — Deterministic Routing + Typed Settings** ✅
- Model catalog (`config/models.yaml` → `core/model_catalog.py`), typed settings, task types, routing policy.
- LLM router with `complete()`, `complete_task()`, `complete_with_escalation()`.
- Hook bus for prompt/response observability.
- Four provider adapters: OpenAI, Anthropic, Grok, Ollama.
- Error hierarchy, startup validation, frozen dataclass value objects.

**Phase 2 — Webhook Ingress + GitHub App Auth + Queue** ✅
- FastAPI ingress (`server/app.py`) with `/health` and `/webhook` endpoints.
- Webhook signature verification via HMAC SHA-256.
- GitHub App JWT generation and installation token retrieval (`github/auth.py`).
- Event normalization into `NormalizedEvent` envelopes (`github/events.py`).
- In-memory async job queue abstraction (`core/job_queue.py`), swappable for Redis/Celery later.

**Phase 3 — GitHub Client + Diff Parser + Read-Only Skills** ✅
- Async read-only GitHub REST client (`github/client.py`) with typed return objects, pagination, and error mapping.
- Separate GitHub error hierarchy (`github/errors.py`) — does not inherit from `LLMRouterError`.
- Pure unified diff parser (`github/diff_parser.py`) handling binary, rename, add, delete, truncated diffs.
- Skill framework (`skills/base.py`): `SkillContext`, generic `SkillResult[T]`, `BaseSkill` ABC.
- Typed decision parsing (`skills/decisions.py`) with `from_llm_response()` and `make_decision_validator()` factory.
- Two read-only skills: `PRTriageSkill` (Option B routing) and `IssueTriageSkill`.
- Prompt templates loaded via `importlib.resources` from the `prompts` package.
- Minimal skill dispatcher (`core/skill_dispatcher.py`) consuming the shared queue, wired into FastAPI lifespan.
- **No GitHub write operations** — Phase 3 is strictly read + reason.

### Next Up

**Phase 4 — Orchestrator + First Write Actions (Idempotent)**
- Full orchestrator (`core/orchestrator.py`) replacing the minimal dispatcher.
- Idempotency layer (`core/idempotency.py`): key = delivery ID + action fingerprint.
- Action policy (`core/action_policy.py`) with DRY_RUN mode as mandatory feature flag.
- Write methods on GitHub client: `create_issue_comment()`, `add_labels()`, `create_pr_review_summary()`.
- Write-capable skills: `skills/pr_summary.py`, `skills/issue_label.py`, `skills/issue_response.py`.
- Repo + event type allowlist.

**Phase 5 — Controlled Auto-Fix Pipeline (Branch/Commit/PR)**
- Patch worker, git operations, safety rules (path/diff/size).
- Allowed command templates only (ruff, mypy, pytest). No arbitrary execution from model output.
- Run metadata persistence (SQLite for v1).

**Phase 6 — Deployment + Observability + Portfolio Polish**
- Dockerfile, docker-compose, deployment docs.
- Metrics/logging: event_id, delivery_id, selected_model, escalation_count, latency.
- Container smoke tests, staging repo soak tests.

### Explicitly Out of Scope for v1
- Multi-agent orchestration, memory/history graph, line-level review comments, autonomous merges, GraphQL, UI dashboard.

## Build & Development Commands

```bash
# Install (editable, with dev deps)
python -m pip install -e '.[dev]'

# Copy env template
cp .env.example .env

# Lint (ruff + mypy strict)
make lint

# Run all tests
make test

# Run a single test file
python -m pytest tests/core/test_llm_router.py

# Run a single test by name
python -m pytest tests/core/test_llm_router.py -k test_router_uses_defaults

# Bootstrap CLI validation (checks DEFAULT_PROVIDER/DEFAULT_MODEL against catalog)
make run

# Local/offline mode with Ollama
make run-local
```

## Architecture

### End-to-End Request Flow (Phase 3)

```
GitHub webhook
  → FastAPI ingress (server/app.py)
  → HMAC signature verification (server/webhooks.py)
  → Event normalization (github/events.py → NormalizedEvent)
  → Async job queue (core/job_queue.py)
  → Skill dispatcher (core/skill_dispatcher.py)
      → Match event to skills
      → Per-event: generate GitHub App JWT → fetch installation token → create GitHubClient
      → Execute matching skills sequentially
      → Log SkillResult as structured JSON
```

### LLM Routing Pipeline

Deterministic and policy-based — model choice is never delegated to an LLM.

1. **Model Catalog** (`config/models.yaml` → `core/model_catalog.py`): YAML-defined model descriptors with provider, context window, cost tier, and `suited_for` task types.
2. **Task Types** (`core/task_types.py`): `TaskType` enum and `TaskComplexity` enum drive all routing.
3. **Routing Policy** (`core/routing_policy.py`): Multi-key sort (tier distance → preferred provider → local preference → cost → context window → name). Fixed escalation chains: low→medium→high.
4. **LLM Router** (`core/llm_router.py`): `complete()` (direct), `complete_task()` (routed), `complete_with_escalation()` (tiered with validation callback).
5. **Hook Bus** (`core/hooks.py`): Async `on_llm_prompt` and `on_llm_response` hooks.

### Skill Pipeline (Phase 3)

Skills are the "brain" that decides what to do with a routed event:

- **Skill framework** (`skills/base.py`): `SkillContext` (event + client + router + logger), generic `SkillResult[T]`, `BaseSkill` ABC with `handles_event()` and `execute()`.
- **Decision types** (`skills/decisions.py`): `PRTriageDecision` and `IssueTriageDecision` — frozen dataclasses with `from_llm_response(content: str) -> Self` for strict JSON parsing. `make_decision_validator()` factory produces a `ResponseValidator` compatible with `complete_with_escalation()`.
- **PR triage routing** (Option B in `skills/pr_triage.py`):
  - Small PR (<50 changed lines, ≤3 files) → `TaskType.TRIAGE` / `TaskComplexity.LOW`
  - Medium PR (<300 changed lines, ≤10 files) → `TaskType.DEEP_REVIEW` / `TaskComplexity.MEDIUM`
  - Large PR → `TaskType.DEEP_REVIEW` / `TaskComplexity.HIGH`
- **Issue triage** (`skills/issue_triage.py`): Always `TaskType.TRIAGE` / `TaskComplexity.LOW`.
- **Prompt templates** (`prompts/pr_triage.md`, `prompts/issue_triage.md`): Loaded via `importlib.resources`, instruct the model to output raw JSON only.

### GitHub Client Layer (Phase 3)

- **Error hierarchy** (`github/errors.py`): `GitHubClientError` → `GitHubAuthenticationError` (401), `GitHubRateLimitError` (403 + rate limit), `GitHubResourceNotFoundError` (404), `GitHubValidationError` (422), `GitHubTransientError` (502/503/504). Separate from `LLMRouterError`.
- **Diff parser** (`github/diff_parser.py`): `DiffLine`, `DiffHunk`, `FileDiff`, `parse_diff()`. Handles binary, rename, new/deleted files, no-newline-at-EOF, empty/truncated diffs.
- **REST client** (`github/client.py`): Async context manager wrapping `httpx.AsyncClient`. Read-only methods return frozen dataclasses (`PullRequest`, `PullRequestFile`, `Issue`, `IssueComment`). Pagination on `get_issue_comments()` and `get_pull_request_files()` (Link header, max 10 pages / 300 items).

### Skill Dispatcher (Phase 3)

`core/skill_dispatcher.py` — minimal event-to-skill dispatcher (not the Phase 4 orchestrator):
- Lifespan-managed async task started in `server/app.py`
- Consumes the same `InMemoryJobQueue` that the webhook handler produces to
- Per-event: fresh installation token → `GitHubClient` as async context manager → sequential skill execution
- Logs each `SkillResult` as structured JSON with event_type, delivery_id, repository, skill_name, model, task_type, complexity, confidence, elapsed_seconds, decision, recommended_actions
- No idempotency, no action policy, no writes, no retry framework, no plugin discovery

### Webhook Payload Key Paths

Skills extract data from `NormalizedEvent.payload` (raw GitHub webhook JSON):
```python
# PR events:
owner = event.payload["repository"]["owner"]["login"]
repo = event.payload["repository"]["name"]
pr_number = event.payload["pull_request"]["number"]

# Issue events:
owner = event.payload["repository"]["owner"]["login"]
repo = event.payload["repository"]["name"]
issue_number = event.payload["issue"]["number"]

# Sender (not on NormalizedEvent directly):
sender_login = event.payload["sender"]["login"]
```

### Existing Signatures to Call Into

```python
# core/llm_router.py — positional-or-keyword args, NOT keyword-only
complete_with_escalation(self, system, messages, max_tokens, temperature,
    task_type, complexity, validate, hint=None) -> LLMResponse

complete_task(self, system, messages, max_tokens, temperature,
    task_type, complexity, hint=None) -> LLMResponse

# github/auth.py — keyword-only args
generate_github_app_jwt(*, app_id, private_key_pem) -> str
fetch_installation_access_token(*, app_jwt, installation_id, base_url=..., timeout_seconds=..., client=None) -> InstallationAccessToken
```

## Key Conventions

- **Python ≥3.12** required. Uses `StrEnum`, `slots=True` dataclasses, `from __future__ import annotations` everywhere.
- **mypy strict mode** — all code must pass `mypy .` with strict enabled.
- **ruff** for linting and formatting. Line length: 100.
- **pytest-asyncio** with `asyncio_mode = "auto"` — async tests use `@pytest.mark.asyncio` by convention.
- **Frozen dataclasses** (`frozen=True, slots=True`) for all value objects.
- **Error hierarchy**: `LLMRouterError` tree for router/provider errors, separate `GitHubClientError` tree for GitHub API errors, `SkillError` tree for skill execution/parsing errors.
- **Tests use fake providers** (not mocks) — `FakeProvider` subclasses `BaseLLMProvider` and returns canned `LLMResponse` objects. For skill tests, variants return canned JSON matching the decision schema.
- **respx** for httpx mocking in GitHub client and skill tests.
- **`json.loads()` returns `Any`** — when declaring typed return values, assign to an explicitly typed intermediate variable to satisfy mypy strict (e.g., `data: dict[str, Any] = json.loads(...)`).

## Key Design Invariants

- Routing is deterministic and policy-driven. The LLM router is the execution engine, not the policy engine.
- The webhook handler validates first and enqueues only — no heavy work inline.
- GitHub events must be normalized into internal envelopes before downstream processing.
- The queue interface must remain swappable.
- Validation errors must be explicit and typed.
- Startup should fail fast if defaults and catalog disagree.
- No future phase should silently bypass signature verification.
- Idempotency is required before any GitHub write actions (Phase 4).
- DRY_RUN mode must exist before enabling writes (Phase 4).
- No secrets in logs.
- Phase 3 is strictly read-only — no GitHub writes anywhere.

## Adding New Providers

See `CONTRIBUTING.md`. Key steps: implement `BaseLLMProvider`, register factory in `LLMRouter._default_factories()`, add env vars to `.env.example`, add model entries to `config/models.yaml`.

## Adding New Skills

1. Create decision type in `skills/decisions.py` with `from_llm_response(cls, content: str) -> Self`.
2. Create prompt template in `prompts/` (raw JSON output, no markdown fences).
3. Implement skill in `skills/` subclassing `BaseSkill`.
4. Use `make_decision_validator(DecisionClass)` for escalation-on-parse-failure.
5. Register in the dispatcher's skill list in `server/app.py`.
6. Add tests with `FakeProvider` variant + respx mocking + golden output fixtures.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on PRs and pushes to main: startup validation → ruff → mypy → pytest.

## Repository Evolution

- `40726f8` — Bootstrapped repository, packaging, CI, and project scaffolding.
- `e02bd9f` — Established the multi-provider LLM runtime foundation.
- `aa5643e` — Completed the deterministic routing foundation and documented the phased roadmap.
- `89c358f` — Rewrote the README with clearer project narrative and implementation context.
- Phase 2 (uncommitted) — Added webhook ingress, GitHub App auth, event normalization, async queue.
- Phase 3 (uncommitted) — Added GitHub client, diff parser, skill framework, read-only triage skills, dispatcher.
