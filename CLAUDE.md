# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

GitHub Auto-Maintainer is a policy-driven automation system for GitHub repository maintenance (triage, routing, summarization, follow-up). The core focus is deterministic, auditable model routing—not raw LLM generation quality.

Design goals:
- Observe GitHub events safely.
- Normalize them into typed internal objects.
- Choose models deterministically.
- Validate before acting.
- Keep write actions idempotent and test-backed.

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

**Phase 4 — Orchestrator + First Write Actions (Idempotent)** ✅
- Action protocol (`core/actions.py`): `ActionRequest` protocol with `action_type` property and `fingerprint()` method. Three concrete frozen dataclasses: `IssueCommentAction`, `AddLabelsAction`, `PRReviewSummaryAction`. `IssueCommentAction` and `PRReviewSummaryAction` fingerprints include a SHA-256 body content hash for deduplication accuracy.
- Idempotency layer (`core/idempotency.py`): `IdempotencyStore` protocol, `InMemoryIdempotencyStore`, `build_idempotency_key()` combining delivery ID + action fingerprint.
- Action policy (`core/action_policy.py`): `ActionPolicy` with `DRY_RUN` mode (default `true` via env var), repo allowlist (`GITHUB_ALLOWED_REPOSITORIES`), event allowlist (`GITHUB_ALLOWED_EVENTS`).
- Write methods on GitHub client: `create_issue_comment()`, `add_labels()`, `create_pr_review_summary()`. New return type: `PullRequestReview`.
- Three write-capable skills: `PRSummarySkill` (`skills/pr_summary.py`), `IssueLabelSkill` (`skills/issue_label.py`), `IssueResponseSkill` (`skills/issue_response.py`). Each returns `SkillResult` with `planned_actions`.
- Three new decision types: `PRSummaryDecision`, `IssueLabelDecision`, `IssueResponseDecision`.
- Three new prompt templates: `prompts/pr_summary.md`, `prompts/issue_label.md`, `prompts/issue_response.md`.
- Typed payload extraction helpers (`skills/payload.py`).
- Full orchestrator (`core/orchestrator.py`) replacing the Phase 3 skill dispatcher in runtime use. Adds allowlist gating → skill matching → installation check → skill execution → action execution with idempotency and dry-run.
- Server wiring updated: `server/app.py` uses `Orchestrator` with write skills; `__main__.py` starts uvicorn.
- Integration tests (`tests/integration/`) covering happy path, duplicate replay, dry-run, and allowlist rejection.

**Phase 5 — Controlled Auto-Fix Pipeline (Branch/Commit/PR)** ✅
- Automation package (`automation/`): safety guardrails (`safety.py`), GitHub REST-based git operations (`git_ops.py`), `AutoFixSkill` patch worker (`patch_worker.py`), async check runner for future use (`check_runner.py`).
- New error hierarchy (`core/errors.py`): `AutomationError` base (inherits from `Exception` directly — NOT from `LLMRouterError`, `SkillError`, or `GitHubClientError`) → `SafetyError`, `PatchConflictError`, `CheckFailureError`.
- Three new action types (`core/actions.py`): `CreateBranchAction`, `CommitPatchAction`, `CreatePullRequestAction` with `PatchFileSummary` value object. Deterministic fingerprints with SHA-256 content hashing.
- Five new GitHub client methods (`github/client.py`): `get_file_content()`, `create_or_update_file()`, `create_branch()`, `get_branch_ref()`, `create_pull_request()`. Four new return types: `FileContent`, `FileCommitResult`, `BranchRef`, `CreatedPullRequest`.
- SQLite-backed run metadata persistence (`core/run_store.py`): `RunStatus` enum, `AutoFixRun` frozen dataclass, `RunStore` protocol, `InMemoryRunStore`, `SQLiteRunStore` (aiosqlite).
- `AutoFixSkill` (`automation/patch_worker.py`): Triggered by `issues.labeled` (auto-fix label) or `issue_comment.created` (/auto-fix command). Pipeline: fetch issue → LLM patch generation → safety validation → branch/commit/PR via REST API → follow-up issue comment.
- `PatchGenerationDecision` and `PatchFileSpec` decision types (`skills/decisions.py`) with `from_llm_response()` parsing. Prompt template: `prompts/auto_fix.md`.
- Safety module (`automation/safety.py`): blocked paths/extensions, diff size limits, allowed command templates (ruff, mypy, pytest), path traversal rejection.
- Server wiring: `AutoFixSkill` conditionally enabled via `AUTO_FIX_ENABLED` env var, with `SQLiteRunStore` persistence.
- Integration tests (`tests/integration/test_auto_fix_pipeline.py`) covering happy path, LLM rejection, safety rejection, dry-run, allowlist rejection, and run store persistence.

### Next Up

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

# Start the server (validates config, then runs uvicorn on port 8000)
make run

# Local/offline mode with Ollama
make run-local
```

## Architecture

### End-to-End Request Flow

```
GitHub webhook
  → FastAPI ingress (server/app.py)
  → HMAC signature verification (server/webhooks.py)
  → Event normalization (github/events.py → NormalizedEvent)
  → Async job queue (core/job_queue.py)
  → Orchestrator (core/orchestrator.py)
      → Allowlist check (repo + event type via ActionPolicy)
      → Match event to skills
      → Per-event: generate GitHub App JWT → fetch installation token → create GitHubClient
      → Execute matching skills sequentially, collect planned_actions
      → Execute actions with idempotency check + dry-run gate
      → Log SkillResult and action outcomes as structured JSON

Auto-fix path (Phase 5):
  issues.labeled "auto-fix" OR issue_comment.created "/auto-fix"
      → AutoFixSkill
          → Fetch issue details
          → LLM patch generation (PatchGenerationDecision)
          → Safety validation (blocked paths, diff size, extensions)
          → Git operations via REST API (create branch → commit files → open PR)
          → Follow-up IssueCommentAction with PR link
          → Run metadata persisted to SQLite
```

### LLM Routing Pipeline

Deterministic and policy-based — model choice is never delegated to an LLM.

1. **Model Catalog** (`config/models.yaml` → `core/model_catalog.py`): YAML-defined model descriptors with provider, context window, cost tier, and `suited_for` task types.
2. **Task Types** (`core/task_types.py`): `TaskType` enum and `TaskComplexity` enum drive all routing.
3. **Routing Policy** (`core/routing_policy.py`): Multi-key sort (tier distance → preferred provider → local preference → cost → context window → name). Fixed escalation chains: low→medium→high.
4. **LLM Router** (`core/llm_router.py`): `complete()` (direct), `complete_task()` (routed), `complete_with_escalation()` (tiered with validation callback).
5. **Hook Bus** (`core/hooks.py`): Async `on_llm_prompt` and `on_llm_response` hooks.

### Skill Pipeline

Skills are the "brain" that decides what to do with a routed event:

- **Skill framework** (`skills/base.py`): `SkillContext` (event + client + router + logger), generic `SkillResult[T]`, `BaseSkill` ABC with `handles_event()` and `execute()`.
- **Decision types** (`skills/decisions.py`): `PRTriageDecision`, `IssueTriageDecision`, `PRSummaryDecision`, `IssueLabelDecision`, `IssueResponseDecision`, `PatchGenerationDecision` (with `PatchFileSpec`) — frozen dataclasses with `from_llm_response(content: str) -> Self` for strict JSON parsing. `make_decision_validator()` factory produces a `ResponseValidator` compatible with `complete_with_escalation()`.
- **Read-only skills** (Phase 3): `PRTriageSkill` (Option B routing), `IssueTriageSkill`.
- **Write skills** (Phase 4): `PRSummarySkill`, `IssueLabelSkill`, `IssueResponseSkill`. Each returns `SkillResult` with `planned_actions` list of `ActionRequest` objects.
- **Auto-fix skill** (Phase 5): `AutoFixSkill` (`automation/patch_worker.py`). Triggered by issue label or comment command. Performs git operations directly during `execute()`, returns `IssueCommentAction` follow-up.
- **Payload helpers** (`skills/payload.py`): Typed extraction of owner, repo, PR number, issue number, sender from webhook payloads.
- **Prompt templates** (`prompts/`): `pr_triage.md`, `issue_triage.md`, `pr_summary.md`, `issue_label.md`, `issue_response.md`, `auto_fix.md`. Loaded via `importlib.resources`, instruct the model to output raw JSON only.

### Action Execution Pipeline (Phase 4)

- **Action protocol** (`core/actions.py`): `ActionRequest` protocol with `action_type` property and `fingerprint()` method. Six concrete types: `IssueCommentAction`, `AddLabelsAction`, `PRReviewSummaryAction` (Phase 4), `CreateBranchAction`, `CommitPatchAction`, `CreatePullRequestAction` (Phase 5). Content-aware fingerprints use SHA-256 body hashes where applicable.
- **Idempotency** (`core/idempotency.py`): `IdempotencyStore` protocol, `InMemoryIdempotencyStore`, `build_idempotency_key(delivery_id, action)`. Key = `"{delivery_id}:{action.fingerprint()}"`.
- **Action policy** (`core/action_policy.py`): `DRY_RUN` mode (default `true`), repo allowlist, event allowlist. All configurable via env vars or constructor kwargs.
- **Orchestrator** (`core/orchestrator.py`): Full event-to-action pipeline replacing the Phase 3 dispatcher. Allowlist gating → skill execution → action execution with idempotency + dry-run.

### GitHub Client Layer

- **Error hierarchy** (`github/errors.py`): `GitHubClientError` → `GitHubAuthenticationError` (401), `GitHubRateLimitError` (403 + rate limit), `GitHubResourceNotFoundError` (404), `GitHubConflictError` (409, non-retryable), `GitHubValidationError` (422), `GitHubTransientError` (502/503/504). Separate from `LLMRouterError`.
- **Diff parser** (`github/diff_parser.py`): `DiffLine`, `DiffHunk`, `FileDiff`, `parse_diff()`. Handles binary, rename, new/deleted files, no-newline-at-EOF, empty/truncated diffs.
- **REST client** (`github/client.py`): Async context manager wrapping `httpx.AsyncClient`. Read methods return frozen dataclasses (`PullRequest`, `PullRequestFile`, `Issue`, `IssueComment`, `FileContent`, `BranchRef`). Write methods: `create_issue_comment()`, `add_labels()`, `create_pr_review_summary()` (Phase 4), `create_or_update_file()`, `create_branch()`, `create_pull_request()` (Phase 5). New return types: `FileCommitResult`, `CreatedPullRequest`. Pagination on `get_issue_comments()` and `get_pull_request_files()` (Link header, max 10 pages / 300 items).


### Retry Strategy

- **Retry helper** (`github/retry.py`): `github_retry` decorator using tenacity. Retries `GitHubTransientError` only (502/503/504). Config: 3 attempts, exponential backoff 0.5s→4s, `reraise=True`.
- **GitHub client**: All 13 public methods on `GitHubClient` are decorated with `@github_retry`.
- **Auth**: `fetch_installation_access_token()` raises `GitHubTransientError` on 502/503/504 (before the `InstallationTokenError` catch-all) and is decorated with `@github_retry`. `fetch_repository_installation()` is NOT retried.
- **Non-retryable errors**: 401 (`GitHubAuthenticationError`), 403 (`GitHubRateLimitError`), 404 (`GitHubResourceNotFoundError`), 409 (`GitHubConflictError`), 422 (`GitHubValidationError`).
- **Orchestrator**: No retry in the orchestrator — retry is handled at the HTTP layer only.

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

# github/client.py — write methods (Phase 4)
create_issue_comment(self, owner, repo, issue_number, body) -> IssueComment
add_labels(self, owner, repo, issue_number, labels: tuple[str, ...]) -> tuple[str, ...]
create_pr_review_summary(self, owner, repo, pr_number, body, *, event="COMMENT", commit_id=None) -> PullRequestReview

# core/idempotency.py
build_idempotency_key(delivery_id: str, action: ActionRequest) -> str

# github/client.py — Phase 5 methods
get_file_content(self, owner, repo, path, *, ref=None) -> FileContent
create_or_update_file(self, owner, repo, path, message, content_b64, *, sha=None, branch=None) -> FileCommitResult
create_branch(self, owner, repo, branch_name, sha) -> BranchRef
get_branch_ref(self, owner, repo, branch) -> BranchRef
create_pull_request(self, owner, repo, title, body, head, base) -> CreatedPullRequest

# automation/git_ops.py — high-level operations
generate_branch_name(prefix, issue_number) -> str
create_fix_branch(client, owner, repo, branch_name, base_branch) -> BranchRef
apply_patches(client, owner, repo, branch_name, patches, commit_message) -> tuple[FileCommitResult, ...]
open_fix_pr(client, owner, repo, title, body, head, base) -> CreatedPullRequest
```

## Key Conventions

- **Python ≥3.12** required. Uses `StrEnum`, `slots=True` dataclasses, `from __future__ import annotations` everywhere.
- **mypy strict mode** — all code must pass `mypy .` with strict enabled.
- **ruff** for linting and formatting. Line length: 100.
- **pytest-asyncio** with `asyncio_mode = "auto"` — async tests use `@pytest.mark.asyncio` by convention.
- **Frozen dataclasses** (`frozen=True, slots=True`) for all value objects.
- **Error hierarchy**: `LLMRouterError` tree for router/provider errors, `GitHubClientError` tree for GitHub API errors, `SkillError` tree for skill execution/parsing errors, `AutomationError` tree for auto-fix pipeline errors. All four trees inherit from `Exception` directly — they are intentionally separate.
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
- Idempotency is enforced before all GitHub write actions (delivery ID + action fingerprint).
- DRY_RUN mode is on by default — must be explicitly set to `false` to enable writes.
- Allowlist gating runs before skill execution to save LLM/API cost.
- Action execution failures do not stop remaining actions in the same event.
- No secrets in logs.
- Safety validation runs before any git write operation in the auto-fix pipeline.
- No subprocess git, no `shell=True`, no GraphQL — all git operations use the GitHub REST API.
- Auto-fix run metadata is persisted before and after every pipeline stage.

## Adding New Providers

See `CONTRIBUTING.md`. Key steps: implement `BaseLLMProvider`, register factory in `LLMRouter._default_factories()`, add env vars to `.env.example`, add model entries to `config/models.yaml`.

## Adding New Skills

1. Create decision type in `skills/decisions.py` with `from_llm_response(cls, content: str) -> Self`.
2. Create prompt template in `prompts/` (raw JSON output, no markdown fences).
3. Implement skill in `skills/` subclassing `BaseSkill`.
4. Use `make_decision_validator(DecisionClass)` for escalation-on-parse-failure.
5. For write skills: return `SkillResult` with `planned_actions` list of `ActionRequest` objects.
6. Register in the orchestrator's skill list in `server/app.py`.
7. Add tests with `FakeProvider` variant + respx mocking + golden output fixtures.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on PRs and pushes to main: startup validation → ruff → mypy → pytest.

## Repository Evolution

- `40726f8` — Bootstrapped repository, packaging, CI, and project scaffolding.
- `e02bd9f` — Established the multi-provider LLM runtime foundation.
- `aa5643e` — Completed the deterministic routing foundation and documented the phased roadmap.
- `89c358f` — Rewrote the README with clearer project narrative and implementation context.
- `720ef50` — Added triage skills and server workflow (Phases 2–3).
- `4d3043c` — Phase 4: Orchestrator, write actions, idempotency, action policy, integration tests.
