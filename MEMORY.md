# MEMORY.md

Project state and decisions for GitHub Auto-Maintainer. Updated as phases complete.

## Current State

**Phases 1–4 complete.** All code passes `make lint` (ruff + mypy strict) and `make test` (211 tests).

Phase 4 is uncommitted — all new files are untracked or modified in the working tree.

## Key Decisions Made

### Routing Strategy (Phase 3) — Option B
- `TaskType.TRIAGE` for issue triage and small PR triage.
- `TaskType.DEEP_REVIEW` for medium and large PR triage.
- PR size thresholds: small (<50 changed lines, ≤3 files), medium (<300 changed lines, ≤10 files), large (everything else).

### Dispatcher Design (Phase 3)
- Minimal event-to-skill dispatcher, not the full Phase 4 orchestrator.
- Lifespan-managed async task consuming the same in-memory queue as `server/app.py`.
- Sequential skill execution per event. No idempotency, no action policy, no writes.
- Explicit skill list passed into dispatcher — no auto-discovery or plugin system.

### Token Handling (Phase 3)
- Per-event fresh installation token. New `GitHubClient` created per event as an async context manager.
- No token caching or token-provider abstraction yet.

### LLM Output Parsing (Phase 3)
- Each decision type defines `from_llm_response(content: str) -> Self`.
- `make_decision_validator()` factory produces `ResponseValidator` (returns True/False) for use with `complete_with_escalation()`.
- Parse failures trigger escalation automatically through the existing router mechanism.
- Unknown JSON fields are silently ignored (consistent across all decision types).

### Error Hierarchies
- **Router/provider errors**: `LLMRouterError` → `TransientProviderError`, `NonRetryableProviderError`, `NoModelCandidateError`.
- **GitHub API errors**: `GitHubClientError` → `GitHubAuthenticationError`, `GitHubRateLimitError`, `GitHubResourceNotFoundError`, `GitHubValidationError`, `GitHubTransientError`. Separate tree, does not inherit from `LLMRouterError`.
- **Skill errors**: `SkillError` → `SkillExecutionError`, `SkillResponseParsingError`. Added to `core/errors.py`.

### Action Protocol (Phase 4)
- `ActionRequest` is a `Protocol` (not ABC) because frozen dataclasses cannot inherit from a frozen dataclass base with fields.
- Each concrete action (`IssueCommentAction`, `AddLabelsAction`, `PRReviewSummaryAction`) is a standalone frozen dataclass satisfying the protocol.
- `fingerprint()` returns a deterministic string used for idempotency keying.

### Idempotency (Phase 4)
- `IdempotencyStore` is a `Protocol` — swappable for Redis/SQLite later.
- `InMemoryIdempotencyStore` for v1 — state lost on restart.
- Key format: `"{delivery_id}:{action.fingerprint()}"`.
- Actions are only marked seen after successful execution. Failed writes are NOT marked seen (allows retry).
- Dry-run mode does NOT mark actions as seen (allows re-processing when dry-run is disabled).

### DRY_RUN Default (Phase 4)
- `DRY_RUN` defaults to `true` via env var. Must be explicitly set to `false` to enable writes.
- This is a safety measure — new deployments are read-only by default.

### Allowlist Gating (Phase 4)
- Allowlist check runs BEFORE skill execution to save LLM/API cost.
- Empty allowlist means "allow all" (permissive default for development).
- Configured via `GITHUB_ALLOWED_REPOSITORIES` and `GITHUB_ALLOWED_EVENTS` env vars (comma-separated).

### Orchestrator Design (Phase 4)
- Full orchestrator replaces the Phase 3 skill dispatcher in runtime use.
- `_process_event()` flow: allowlist check → skill matching → installation check → token fetch → skill execution → action execution.
- Per-skill exception handling: one skill failure does not stop other skills.
- Per-action exception handling: one action failure does not stop other actions.
- Match statement dispatches action types to GitHub client write methods.

### Write Skills (Phase 4)
- `PRSummarySkill`: handles `pull_request.opened`, produces `PRReviewSummaryAction`.
- `IssueLabelSkill`: handles `issues.opened`, produces `AddLabelsAction`.
- `IssueResponseSkill`: handles `issues.opened`, produces `IssueCommentAction`.
- Payload extraction centralized in `skills/payload.py` with typed helper functions.

## File Inventory (Phase 3 additions)

### Production files created
- `src/github_auto_maintainer/github/errors.py` — GitHub error hierarchy
- `src/github_auto_maintainer/github/diff_parser.py` — Unified diff parser
- `src/github_auto_maintainer/github/client.py` — Async read-only REST client
- `src/github_auto_maintainer/skills/base.py` — SkillContext, SkillResult[T], BaseSkill
- `src/github_auto_maintainer/skills/decisions.py` — PRTriageDecision, IssueTriageDecision, make_decision_validator
- `src/github_auto_maintainer/prompts/__init__.py` — Package for importlib.resources
- `src/github_auto_maintainer/prompts/pr_triage.md` — PR triage prompt template
- `src/github_auto_maintainer/prompts/issue_triage.md` — Issue triage prompt template
- `src/github_auto_maintainer/skills/pr_triage.py` — PRTriageSkill (Option B routing)
- `src/github_auto_maintainer/skills/issue_triage.py` — IssueTriageSkill
- `src/github_auto_maintainer/core/skill_dispatcher.py` — Minimal queue consumer + dispatcher

### Production files modified
- `src/github_auto_maintainer/core/errors.py` — Added SkillError, SkillExecutionError, SkillResponseParsingError
- `src/github_auto_maintainer/server/app.py` — Added lifespan wiring for dispatcher
- `pyproject.toml` — Added respx dev dep, setuptools package-data for prompt .md files

### Test files created (Phase 3)
- `tests/github/test_errors.py`
- `tests/github/test_diff_parser.py`
- `tests/github/test_client.py`
- `tests/skills/__init__.py`
- `tests/skills/test_decisions.py`
- `tests/skills/test_pr_triage.py`
- `tests/skills/test_issue_triage.py`
- `tests/core/test_skill_dispatcher.py`

### Fixture files created (Phase 3)
- `tests/fixtures/pr_opened_payload.json`
- `tests/fixtures/issue_opened_payload.json`
- `tests/fixtures/small_diff.patch`
- `tests/fixtures/medium_diff.patch`
- `tests/fixtures/large_diff.patch`
- `tests/fixtures/pr_triage_golden.json`
- `tests/fixtures/issue_triage_golden.json`

## File Inventory (Phase 4 additions)

### Production files created
- `src/github_auto_maintainer/core/actions.py` — ActionRequest protocol, IssueCommentAction, AddLabelsAction, PRReviewSummaryAction
- `src/github_auto_maintainer/core/action_policy.py` — ActionPolicy with DRY_RUN, repo/event allowlists
- `src/github_auto_maintainer/core/idempotency.py` — IdempotencyStore protocol, InMemoryIdempotencyStore, build_idempotency_key
- `src/github_auto_maintainer/core/orchestrator.py` — Full event-to-action orchestrator replacing skill dispatcher
- `src/github_auto_maintainer/skills/payload.py` — Typed payload extraction helpers
- `src/github_auto_maintainer/skills/pr_summary.py` — PRSummarySkill (write)
- `src/github_auto_maintainer/skills/issue_label.py` — IssueLabelSkill (write)
- `src/github_auto_maintainer/skills/issue_response.py` — IssueResponseSkill (write)
- `src/github_auto_maintainer/prompts/pr_summary.md` — PR summary prompt template
- `src/github_auto_maintainer/prompts/issue_label.md` — Issue label prompt template
- `src/github_auto_maintainer/prompts/issue_response.md` — Issue response prompt template

### Production files modified
- `src/github_auto_maintainer/github/client.py` — Added write methods (create_issue_comment, add_labels, create_pr_review_summary) and PullRequestReview return type
- `src/github_auto_maintainer/skills/decisions.py` — Added PRSummaryDecision, IssueLabelDecision, IssueResponseDecision
- `src/github_auto_maintainer/server/app.py` — Replaced SkillDispatcher with Orchestrator, wired write skills, ActionPolicy, InMemoryIdempotencyStore
- `src/github_auto_maintainer/__main__.py` — Added uvicorn.run() after validation

### Test files created (Phase 4)
- `tests/core/test_action_policy.py`
- `tests/core/test_idempotency.py`
- `tests/core/test_test_orchestrator.py`
- `tests/skills/test_decisions_write.py`
- `tests/skills/test_payload.py`
- `tests/skills/test_pr_summary.py`
- `tests/skills/test_issue_label.py`
- `tests/skills/test_issue_response.py`
- `tests/integration/__init__.py`
- `tests/integration/test_webhook_to_orchestrator.py`

### Test files modified (Phase 4)
- `tests/server/test_app.py` — Updated monkeypatch from SkillDispatcher to Orchestrator, **kwargs on _RecordingDispatcher
- `tests/github/test_client.py` — Added tests for write methods

### Fixture files created (Phase 4)
- `tests/fixtures/pr_summary_golden.json`
- `tests/fixtures/issue_label_golden.json`
- `tests/fixtures/issue_response_golden.json`

## What Phase 5 Must Address

- **Patch worker**: safe git operations (branch, commit, PR) with size/path/diff guards.
- **Allowed command templates**: only ruff, mypy, pytest. No arbitrary execution from model output.
- **Run metadata persistence**: SQLite for v1 to track auto-fix attempts and outcomes.
- **Token caching**: consider adding a token-provider abstraction to avoid per-event token fetch overhead.
- **Graceful drain**: worker health management and shutdown signaling.
- **Idempotency store upgrade**: consider SQLite or Redis to survive restarts.

## Testing Patterns

- **FakeProvider**: subclasses `BaseLLMProvider`, returns canned `LLMResponse`. For skill tests, variants return canned JSON matching decision schemas.
- **FakeSkill**: used in orchestrator tests — configurable `handles_event()` and `execute()` returning configurable `SkillResult` with `planned_actions`.
- **respx**: used for httpx mocking in GitHub client, skill, and integration tests.
- **Golden tests**: fixture JSON files in `tests/fixtures/` for validating decision output schemas.
- **`json.loads()` + mypy**: always assign to explicitly typed variable (e.g., `data: dict[str, Any] = json.loads(...)`) to avoid `Returning Any` errors under mypy strict.
- **Integration tests**: create real queue + orchestrator with FakeProvider, mock all GitHub HTTP endpoints via respx, call `_process_event()` directly (not the infinite `run()` loop).
