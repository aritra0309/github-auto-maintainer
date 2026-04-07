# Phase 3.5 — Stability Patch Workflow

> **Goal:** Fix the verified gaps in Phase 3 so it is genuinely trustworthy before Phase 4 adds write actions, idempotency, and the orchestrator.
>
> **Scope rule:** Fix verified bugs and prove the vertical slice. Do not invent new routing behavior, do not expand model capabilities beyond the confirmed gap, do not add Phase 4 features.

---

## Agreed Scope (4 fixes + 1 structural improvement)

| # | Fix | Blocker? | Files touched |
|---|-----|----------|---------------|
| 1 | Private key env contract | **Yes** | `server/app.py`, `tests/server/test_app.py` |
| 2 | TRIAGE escalation gap | **Yes** | `config/models.yaml`, `tests/core/test_routing_real_catalog.py` |
| 3 | Payload extraction robustness | No (but prevents paper-cut failures before Phase 4 writes) | `skills/payload.py`, `skills/pr_triage.py`, `skills/issue_triage.py`, tests |
| 4 | `create_app(router=...)` injectable | No (structural, enables Fix 5) | `server/app.py` |
| 5 | End-to-end integration test | **Yes** | `tests/integration/test_webhook_to_skill.py` |

---

## Execution Order

Each step is independently testable and committable. Run `make lint && make test` after every step before proceeding.

---

### Step 1 — TRIAGE escalation gap in `config/models.yaml`

**The bug:** When `PRTriageSkill` routes a small PR as `TaskType.TRIAGE` / `TaskComplexity.LOW` and the LLM response fails validation, `complete_with_escalation()` escalates to `MEDIUM`. The routing policy calls `select(task_type=TRIAGE, complexity=MEDIUM)`. There is no medium-tier model with `triage` in its `suited_for`:

```
claude-haiku-4-5  → low    → triage ✓
claude-sonnet-4-6 → medium → patch_generation, deep_review, architecture, agentic_workflows  ✗
claude-opus-4-6   → high   → complex_reasoning, long_horizon_tasks, enterprise_agents  ✗
```

This means TRIAGE escalation hits `NoModelCandidateError` — a runtime crash on a normal code path.

**The fix:** Add `triage` to `claude-sonnet-4-6`'s `suited_for` list. Nothing else.

**What NOT to do:**
- Do not add `summarization` to Sonnet (no skill uses `TaskType.SUMMARIZATION` yet).
- Do not add `triage` or `summarization` to `qwen3-coder-next:14b` (it is positioned as local code-review / patch-generation; changing that alters routing semantics beyond the verified bug).
- Do not add `triage` to any high-tier model (that is a separate design decision for a future phase).

**File changes:**

`config/models.yaml` — `claude-sonnet-4-6` entry becomes:
```yaml
  - provider: anthropic
    model: claude-sonnet-4-6
    context_window: 1000000
    cost_tier: medium
    suited_for:
      - triage
      - patch_generation
      - deep_review
      - architecture
      - agentic_workflows
```

**Test:** `tests/core/test_routing_real_catalog.py`

```python
"""Regression: real shipped catalog supports TRIAGE escalation to MEDIUM."""

from pathlib import Path

import pytest

from github_auto_maintainer.core.model_catalog import ModelCatalog
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType

_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


@pytest.fixture()
def real_policy() -> RoutingPolicy:
    catalog = ModelCatalog.from_yaml(_CATALOG_PATH)
    return RoutingPolicy(catalog)


def test_triage_medium_selects_sonnet(real_policy: RoutingPolicy) -> None:
    """TRIAGE at MEDIUM must resolve to claude-sonnet-4-6 (the verified escalation target)."""
    model = real_policy.select(task_type=TaskType.TRIAGE, complexity=TaskComplexity.MEDIUM)
    assert model.model == "claude-sonnet-4-6"
    assert model.cost_tier == TaskComplexity.MEDIUM


def test_triage_low_selects_haiku(real_policy: RoutingPolicy) -> None:
    """TRIAGE at LOW must still resolve to claude-haiku (existing behavior, regression guard)."""
    model = real_policy.select(task_type=TaskType.TRIAGE, complexity=TaskComplexity.LOW)
    assert model.model == "claude-haiku-4-5-20251001"
    assert model.cost_tier == TaskComplexity.LOW
```

> **Note:** We intentionally do NOT test `TRIAGE` at `HIGH` because there is no high-tier triage model in the current catalog. That is a known gap, but it is not a Phase 3.5 blocker — the escalation chain in `complete_with_escalation()` will raise `NoModelCandidateError` at HIGH, which is acceptable behavior for now. Phase 4's orchestrator can decide how to handle that.

**Gate:** `make lint && make test` — all green.

---

### Step 2 — Private key env contract in `server/app.py`

**The bug:** `server/app.py:43` reads:
```python
private_key_pem = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
```

But the documented contract (`.env.example`) is `GITHUB_APP_PRIVATE_KEY_PATH`, and `github/auth.py` already ships `load_private_key_pem(path)` — a path-based loader that is currently dead code.

Reading a raw multi-line PEM from an env var breaks in most shell environments. The app should read a file path, then load the PEM from disk.

**The fix:** In the lifespan function, replace the raw env var read with path-based loading.

**Behavior contract:**
| Condition | Behavior |
|-----------|----------|
| `GITHUB_APP_PRIVATE_KEY_PATH` empty or unset | Dispatcher does not start. Warning log. App still serves `/health` and `/webhook` (queue accepts events, they just won't be processed). |
| Path set but file does not exist | Dispatcher does not start. Warning log. Same as above. |
| Path set, file exists, but unreadable (permissions) | **Startup fails loudly.** `load_private_key_pem()` raises `OSError`, which propagates. This is broken config, not absent config. |
| Path set, file exists, readable | PEM loaded. Dispatcher starts normally. |

**Do NOT update `.env.example`** — it already documents `GITHUB_APP_PRIVATE_KEY_PATH` correctly.

**File changes:**

`src/github_auto_maintainer/server/app.py` — lifespan rewrite:

```python
from pathlib import Path
from github_auto_maintainer.github.auth import load_private_key_pem

# Inside create_app():

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app_id = os.getenv("GITHUB_APP_ID", "")
    key_path_raw = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "")
    logger: structlog.stdlib.BoundLogger = structlog.get_logger()

    dispatcher_task: asyncio.Task[None] | None = None

    if not app_id:
        logger.warning("skill_dispatcher.skipped", reason="GITHUB_APP_ID not set")
    elif not key_path_raw:
        logger.warning(
            "skill_dispatcher.skipped",
            reason="GITHUB_APP_PRIVATE_KEY_PATH not set",
        )
    elif not Path(key_path_raw).is_file():
        logger.warning(
            "skill_dispatcher.skipped",
            reason=f"Private key file not found: {key_path_raw}",
        )
    else:
        # Path exists — load_private_key_pem will raise on permission errors (fail-fast)
        private_key_pem = load_private_key_pem(key_path_raw)

        resolved_router = injected_router or LLMRouter()
        dispatcher = SkillDispatcher(
            queue=job_queue,
            skills=[PRTriageSkill(), IssueTriageSkill()],
            router=resolved_router,
            app_id=app_id,
            private_key_pem=private_key_pem,
            logger=logger,
        )
        dispatcher_task = asyncio.create_task(dispatcher.run())

    yield

    if dispatcher_task is not None:
        dispatcher_task.cancel()
        try:
            await dispatcher_task
        except asyncio.CancelledError:
            pass
```

> **Note:** The `injected_router` variable comes from Step 4 (`create_app(router=...)`). If implementing Step 2 before Step 4, use `LLMRouter()` directly and refactor in Step 4.

**Test:** `tests/server/test_app.py` — add lifespan tests:

```python
"""Test private key loading behavior in app lifespan."""

# Test 1: Missing GITHUB_APP_PRIVATE_KEY_PATH → dispatcher does not start, no crash
# Test 2: Path set to nonexistent file → dispatcher does not start, warning logged
# Test 3: Path set to valid PEM file → load_private_key_pem called, dispatcher starts
# Test 4: GITHUB_APP_ID set but key path missing → warning log mentions both
```

Use `monkeypatch.setenv()` for env vars and `tmp_path` for fixture PEM files. Use a recording structlog handler to assert warning messages.

**Gate:** `make lint && make test` — all green.

---

### Step 3 — Payload extraction robustness in skills

**The bug:** Both `pr_triage.py:64-66` and `issue_triage.py:52-54` use bare dict access:

```python
owner: str = payload["repository"]["owner"]["login"]
repo: str = payload["repository"]["name"]
pr_number: int = payload["pull_request"]["number"]
```

A malformed or unexpected payload raises a raw `KeyError` that bubbles up as an untyped exception. Since Phase 4 will build write actions on top of these same payload paths, this should be `SkillExecutionError` with a clear message.

**The fix:** Small helper in `skills/payload.py` (NOT `skills/base.py` — `base.py` is the skill framework contract; payload extraction is a separate concern):

```python
"""Payload field extraction with typed error handling."""

from __future__ import annotations

from typing import Any

from github_auto_maintainer.core.errors import SkillExecutionError


def extract_payload_field(payload: dict[str, Any], *keys: str) -> Any:
    """Walk nested dict keys, raising SkillExecutionError on missing fields.

    Usage:
        owner = extract_payload_field(payload, "repository", "owner", "login")
    """
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            path = ".".join(keys)
            raise SkillExecutionError(f"Missing required payload field: {path}")
        current = current[key]
    return current
```

**Update `pr_triage.py`:**
```python
from github_auto_maintainer.skills.payload import extract_payload_field

# Replace:
#   owner: str = payload["repository"]["owner"]["login"]
#   repo: str = payload["repository"]["name"]
#   pr_number: int = payload["pull_request"]["number"]
# With:
owner: str = extract_payload_field(payload, "repository", "owner", "login")
repo: str = extract_payload_field(payload, "repository", "name")
pr_number: int = extract_payload_field(payload, "pull_request", "number")
```

**Update `issue_triage.py`:** Same pattern for `repository.owner.login`, `repository.name`, `issue.number`.

**Tests:** `tests/skills/test_payload.py`

```python
"""Tests for payload field extraction helper."""

# Test 1: Valid nested path returns correct value
# Test 2: Missing top-level key → SkillExecutionError with "Missing required payload field: ..."
# Test 3: Missing deeply nested key → SkillExecutionError with full dotted path
# Test 4: Non-dict intermediate value → SkillExecutionError
# Test 5: Empty payload → SkillExecutionError
```

**Gate:** `make lint && make test` — all green.

---

### Step 4 — `create_app(router=...)` injectable parameter

**The structural improvement:** `create_app()` currently accepts `queue` and `webhook_secret` as injectable dependencies, but constructs `LLMRouter()` internally. This makes integration testing require monkeypatching.

Adding `router: LLMRouter | None = None` follows the existing pattern and makes the integration test (Step 5) clean.

**File changes:**

`src/github_auto_maintainer/server/app.py` — update `create_app` signature:

```python
def create_app(
    *,
    queue: JobQueue[NormalizedEvent] | None = None,
    webhook_secret: str | None = None,
    router: LLMRouter | None = None,
) -> FastAPI:
    """Create the webhook ingress app with injectable dependencies."""

    job_queue: JobQueue[NormalizedEvent] = queue or InMemoryJobQueue[NormalizedEvent]()
    configured_secret = webhook_secret
    injected_router = router

    # ... rest of create_app, lifespan uses `injected_router or LLMRouter()`
```

This is backwards-compatible: existing callers and `app = create_app()` at module level still work.

**Gate:** `make lint && make test` — all green. No new tests needed; this is a signature change that enables Step 5.

---

### Step 5 — End-to-end integration test

**The gap:** There is currently no test proving the full vertical slice: `HTTP POST → signature verification → event normalization → queue → dispatcher → skill execution → structured log output`.

This is the most important proof before Phase 4.

**Implementation details:**

1. **Lifespan management:** `httpx.ASGITransport` does NOT start FastAPI lifespan by itself. The test must wrap with `async with app.router.lifespan_context(app):`.

2. **FakeProvider injection:** Use `create_app(router=...)` from Step 4 to inject a pre-built `LLMRouter` backed by a `FakeProvider` that returns canned JSON matching `PRTriageDecision` schema.

3. **Signal-based assertions (no sleeps):**
   - Wrap `InMemoryJobQueue` to set an `asyncio.Event` on `enqueue`
   - Use a recording log handler that sets an `asyncio.Event` when `skill_dispatcher.skill_result` is logged
   - `await asyncio.wait_for(event, timeout=5.0)` — deterministic, no sleeps

4. **What the test proves:**
   ```
   POST /webhook (valid HMAC signature, PR opened payload)
     → 202 Accepted
     → event enqueued (verified via queue wrapper event)
     → dispatcher dequeues and processes
     → PRTriageSkill executes with FakeProvider
     → structured log emitted with correct schema fields
   ```

**File:** `tests/integration/test_webhook_to_skill.py`

```python
"""End-to-end: webhook POST → queue → dispatcher → skill → structured log."""

# Fixtures needed:
#   - fake_router: LLMRouter with FakeProvider returning valid PRTriageDecision JSON
#   - signaling_queue: InMemoryJobQueue subclass that sets asyncio.Event on enqueue
#   - recording_handler: structlog handler that captures "skill_dispatcher.skill_result"
#     and sets asyncio.Event
#   - valid_pr_payload: dict matching GitHub pull_request.opened webhook shape
#   - webhook_secret: known secret for HMAC signing
#   - tmp_path PEM file for GITHUB_APP_PRIVATE_KEY_PATH

# Test flow:
#   1. app = create_app(queue=signaling_queue, webhook_secret=secret, router=fake_router)
#   2. monkeypatch env: GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH
#   3. monkeypatch github auth functions (generate_github_app_jwt, fetch_installation_access_token)
#      to return canned values — we are NOT testing auth here
#   4. monkeypatch GitHubClient methods (get_pull_request, get_pull_request_diff) to return
#      canned data — we are NOT testing the GitHub API here
#   5. async with app.router.lifespan_context(app):
#        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
#          body = json.dumps(valid_pr_payload).encode()
#          signature = compute_hmac(secret, body)
#          response = await client.post("/webhook", content=body, headers={...})
#          assert response.status_code == 202
#          await asyncio.wait_for(enqueue_event, timeout=5.0)
#          await asyncio.wait_for(skill_result_event, timeout=5.0)
#          assert recording_handler captured expected fields
```

**Key assertions on the captured log:**
- `event_type` == `"pull_request.opened"`
- `skill_name` == `"pr_triage"`
- `model` is the FakeProvider's model name
- `task_type` and `complexity` are present
- `decision` dict has the expected PRTriageDecision fields
- `recommended_actions` is a list

**What this test does NOT cover (and shouldn't):**
- Real GitHub API calls (mocked)
- Real LLM calls (FakeProvider)
- Real GitHub App auth (mocked)
- Idempotency (Phase 4)
- Write actions (Phase 4)

**Gate:** `make lint && make test` — all green.

---

### Step 6 — Final validation

1. Run full `make lint && make test` from clean state.
2. Review all changes as a single diff — verify no scope creep.
3. Verify CLAUDE.md still accurately describes Phase 3 status.
4. Commit as one atomic Phase 3.5 commit with message:

```
Phase 3.5: stability patch for Phase 4 readiness

- Fix TRIAGE escalation gap: add triage to claude-sonnet-4-6 suited_for
- Fix private key env contract: GITHUB_APP_PRIVATE_KEY_PATH + load_private_key_pem()
- Add payload extraction helper: SkillExecutionError on malformed payloads
- Make create_app() accept injectable router parameter
- Add end-to-end integration test: webhook → queue → dispatcher → skill → log
```

---

## Explicitly Out of Scope

| Item | Why |
|------|-----|
| `summarization` added to any model | No skill uses `TaskType.SUMMARIZATION` yet |
| `triage` added to `qwen3-coder-next:14b` | Changes Ollama model's routing identity beyond verified bug |
| `triage` added to any high-tier model | Separate design decision, not a Phase 3 gap |
| `.env.example` changes | Already correct |
| Broad "all-tier escalation chain" test | Would fail at HIGH (no high-tier triage model) — that's known, not a bug |
| Retry framework | Phase 4 |
| Idempotency layer | Phase 4 |
| Plugin discovery for skills | Phase 4 |
| Rewriting flaky dispatcher sleep/cancel tests | Nice-to-have, not a blocker |

---

## Files Changed Summary

```
Modified:
  config/models.yaml                                    (Step 1)
  src/github_auto_maintainer/server/app.py              (Steps 2, 4)
  src/github_auto_maintainer/skills/pr_triage.py        (Step 3)
  src/github_auto_maintainer/skills/issue_triage.py     (Step 3)

Created:
  src/github_auto_maintainer/skills/payload.py          (Step 3)
  tests/core/test_routing_real_catalog.py               (Step 1)
  tests/server/test_app.py                              (Step 2) — or extended if exists
  tests/skills/test_payload.py                          (Step 3)
  tests/integration/test_webhook_to_skill.py            (Step 5)
```

---

## Definition of Done

- [ ] `config/models.yaml` has `triage` in `claude-sonnet-4-6` `suited_for`
- [ ] Real-catalog regression test asserts TRIAGE+MEDIUM → Sonnet
- [ ] `server/app.py` reads `GITHUB_APP_PRIVATE_KEY_PATH` and calls `load_private_key_pem()`
- [ ] Missing key path → warning + skip (not crash). Bad permissions → fail loud.
- [ ] `create_app()` accepts `router: LLMRouter | None = None`
- [ ] `skills/payload.py` exists with `extract_payload_field()`
- [ ] `pr_triage.py` and `issue_triage.py` use `extract_payload_field()`
- [ ] Malformed payload → `SkillExecutionError` (not raw `KeyError`)
- [ ] Integration test proves: POST `/webhook` → 202 → skill executes → structured log emitted
- [ ] Integration test uses `asyncio.Event` assertions (no sleeps)
- [ ] `make lint && make test` passes clean
- [ ] No Phase 4 features leaked in

---

## After Phase 3.5

With this patch applied, Phase 3 is in a trustworthy state for Phase 4 to build on:

- **Routing works end-to-end** including escalation for the task types skills actually use.
- **Config contract is honest** — env vars match what the code reads.
- **Payload failures are typed** — Phase 4 write actions won't build on top of raw `KeyError`s.
- **The vertical slice is proven** — there is a test that exercises the full path from HTTP to structured output.
- **The app is testable** — `create_app(router=...)` means Phase 4's orchestrator tests can inject dependencies cleanly.

Phase 4 can now safely add: orchestrator, idempotency layer, action policy, DRY_RUN mode, and write-capable skills.
