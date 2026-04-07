# MEMORY.md

Project state and decisions for GitHub Auto-Maintainer. Updated as phases complete.

## Current State

**Phases 1–3 complete.** All code passes `make lint` (ruff + mypy strict) and `make test` (119 tests).

Phase 3 is uncommitted — all new files are untracked or modified in the working tree.

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
- Unknown JSON fields are silently ignored (consistent across both decision types).

### Error Hierarchies
- **Router/provider errors**: `LLMRouterError` → `TransientProviderError`, `NonRetryableProviderError`, `NoModelCandidateError`.
- **GitHub API errors**: `GitHubClientError` → `GitHubAuthenticationError`, `GitHubRateLimitError`, `GitHubResourceNotFoundError`, `GitHubValidationError`, `GitHubTransientError`. Separate tree, does not inherit from `LLMRouterError`.
- **Skill errors**: `SkillError` → `SkillExecutionError`, `SkillResponseParsingError`. Added to `core/errors.py`.

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

### Test files created
- `tests/github/test_errors.py`
- `tests/github/test_diff_parser.py`
- `tests/github/test_client.py`
- `tests/skills/__init__.py`
- `tests/skills/test_decisions.py`
- `tests/skills/test_pr_triage.py`
- `tests/skills/test_issue_triage.py`
- `tests/core/test_skill_dispatcher.py`

### Fixture files created
- `tests/fixtures/pr_opened_payload.json`
- `tests/fixtures/issue_opened_payload.json`
- `tests/fixtures/small_diff.patch`
- `tests/fixtures/medium_diff.patch`
- `tests/fixtures/large_diff.patch`
- `tests/fixtures/pr_triage_golden.json`
- `tests/fixtures/issue_triage_golden.json`

## What Phase 4 Must Address

- **Idempotency layer**: delivery ID + action fingerprint before any writes.
- **DRY_RUN mode**: mandatory feature flag before enabling GitHub writes.
- **Full orchestrator**: replace the minimal `SkillDispatcher` with `core/orchestrator.py`.
- **Action policy**: repo + event type allowlist gating write actions.
- **Write methods on GitHubClient**: `create_issue_comment()`, `add_labels()`, `create_pr_review_summary()`.
- **Write-capable skills**: `pr_summary`, `issue_label`, `issue_response`.
- **Token caching**: consider adding a token-provider abstraction to avoid per-event token fetch overhead.
- **Graceful drain**: worker health management and shutdown signaling.

## Testing Patterns

- **FakeProvider**: subclasses `BaseLLMProvider`, returns canned `LLMResponse`. For skill tests, variants return canned JSON matching decision schemas.
- **respx**: used for httpx mocking in GitHub client and skill integration tests.
- **Golden tests**: fixture JSON files in `tests/fixtures/` for validating decision output schemas.
- **`json.loads()` + mypy**: always assign to explicitly typed variable (e.g., `data: dict[str, Any] = json.loads(...)`) to avoid `Returning Any` errors under mypy strict.
