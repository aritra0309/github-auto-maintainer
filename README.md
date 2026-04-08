# GitHub Auto-Maintainer

A policy-driven automation system for GitHub repository maintenance. It reduces repetitive maintainer work — triage, routing, summarization, labeling, and follow-up — while keeping behavior deterministic, testable, and safe.

Built with strict typing, deterministic model selection, explicit safety gates, and clear separation between policy and execution.

## Why this project exists

Maintainers spend substantial time on repeatable but important work: understanding incoming issues and pull requests, prioritizing what to review first, and keeping discussions actionable. AI can help, but in real engineering teams the bigger challenge is not raw generation quality — it is reliability, auditability, and operational control.

This project focuses on that control layer:

- Predictable, policy-driven routing decisions
- Provider and model portability (OpenAI, Anthropic, Grok, Ollama)
- Explicit escalation paths when models fail to produce valid output
- Safe-by-default runtime behavior with mandatory dry-run mode

## How it differs from Copilot, Claude, or Codex-style assistants

Tools like Copilot, Claude, and Codex help a developer in the moment — writing code, reviewing changes, suggesting fixes. GitHub Auto-Maintainer solves a different problem: **what happens around the code**, not inside a single coding session.

- **Event-driven** (webhooks → queue → orchestrator), not prompt-driven
- **Deterministic model routing** — model choice is never delegated to an LLM
- **Multi-provider** — OpenAI, Anthropic, Grok, and Ollama, swappable per task type
- **Safety-first** — signature verification, idempotency, dry-run mode, and allowlists before any write action
- **Auditable** — structured logging of every routing decision, skill result, and action outcome

## What is implemented

### Phase 1 — Deterministic Routing + Typed Settings ✅

Model catalog, typed settings, task types, routing policy. LLM router with `complete()`, `complete_task()`, and `complete_with_escalation()`. Hook bus for observability. Four provider adapters. Error hierarchy, startup validation, frozen dataclass value objects.

### Phase 2 — Webhook Ingress + GitHub App Auth + Queue ✅

FastAPI ingress with `/health` and `/webhook` endpoints. HMAC SHA-256 signature verification. GitHub App JWT generation and installation token retrieval. Event normalization into typed `NormalizedEvent` envelopes. In-memory async job queue (swappable for Redis/Celery).

### Phase 3 — GitHub Client + Diff Parser + Read-Only Skills ✅

Async GitHub REST client with typed return objects, pagination, and error mapping. Pure unified diff parser. Skill framework with `SkillContext`, generic `SkillResult[T]`, and `BaseSkill` ABC. Two read-only triage skills (PR and issue). Prompt templates loaded via `importlib.resources`.

### Phase 4 — Orchestrator + First Write Actions (Idempotent) ✅

Full orchestrator replacing the Phase 3 dispatcher. Three write-capable skills:

| Skill | Trigger | Action |
|---|---|---|
| **PRSummarySkill** | `pull_request.opened` | Posts a review summary comment on the PR |
| **IssueLabelSkill** | `issues.opened` | Adds classification labels to the issue |
| **IssueResponseSkill** | `issues.opened` | Posts an initial triage response comment |

Safety layer:
- **Action protocol** — typed `ActionRequest` with deterministic fingerprinting
- **Idempotency** — delivery ID + action fingerprint prevents duplicate writes
- **Dry-run mode** — enabled by default; writes are blocked until explicitly turned off
- **Allowlist gating** — repo and event type allowlists checked before skill execution (saves LLM/API cost)
- **Fault isolation** — one skill or action failure does not stop others in the same event

**211 tests** pass across unit, skill, orchestrator, and integration test suites.

## End-to-end request flow

```
GitHub webhook
  → FastAPI ingress (HMAC signature verification)
  → Event normalization → NormalizedEvent
  → Async job queue
  → Orchestrator
      → Allowlist check (repo + event type)
      → Match event to skills
      → GitHub App JWT → installation token → GitHubClient
      → Execute skills, collect planned actions
      → Execute actions (idempotency check → dry-run gate → write)
      → Structured JSON logging of every outcome
```

## What is next

**Phase 5 — Controlled Auto-Fix Pipeline**: Patch worker, git operations (branch/commit/PR), safety rules (path/diff/size guards), allowed command templates only (ruff, mypy, pytest — no arbitrary execution), run metadata persistence (SQLite).

**Phase 6 — Deployment + Observability**: Dockerfile, docker-compose, metrics/logging, container smoke tests, staging repo soak tests.

## Run locally

```bash
# Install (editable, with dev deps)
python -m pip install -e '.[dev]'

# Copy env template and set values
cp .env.example .env

# Lint (ruff + mypy strict)
make lint

# Run all tests
make test

# Start the server (dry-run mode by default)
make run
# or: python -m github_auto_maintainer

# Local/offline mode with Ollama
make run-local
```

### Key environment variables

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_PROVIDER` | `openai` | LLM provider for routing |
| `DEFAULT_MODEL` | `gpt-5.4-mini` | Default model name |
| `GITHUB_APP_ID` | — | GitHub App ID |
| `GITHUB_PRIVATE_KEY` | — | GitHub App private key (PEM) |
| `GITHUB_WEBHOOK_SECRET` | — | Webhook HMAC secret |
| `DRY_RUN` | `true` | Block all GitHub writes when `true` |
| `GITHUB_ALLOWED_REPOSITORIES` | _(empty = allow all)_ | Comma-separated repo allowlist |
| `GITHUB_ALLOWED_EVENTS` | _(empty = allow all)_ | Comma-separated event allowlist |

## Repository layout

```
src/github_auto_maintainer/
├── core/                  # Runtime core
│   ├── orchestrator.py    # Event-to-action pipeline
│   ├── llm_router.py      # Deterministic model routing
│   ├── routing_policy.py   # Multi-key model ranking
│   ├── actions.py          # Action protocol + concrete types
│   ├── action_policy.py    # Dry-run, allowlists
│   ├── idempotency.py      # Deduplication layer
│   ├── job_queue.py        # Async queue abstraction
│   ├── model_catalog.py    # YAML model descriptors
│   ├── task_types.py       # TaskType + TaskComplexity enums
│   ├── hooks.py            # LLM observability hooks
│   └── settings.py         # Typed runtime settings
├── github/                # GitHub integration
│   ├── client.py           # Async REST client (read + write)
│   ├── auth.py             # App JWT + installation tokens
│   ├── events.py           # Event normalization
│   ├── diff_parser.py      # Unified diff parser
│   └── errors.py           # GitHub error hierarchy
├── server/                # FastAPI application
│   ├── app.py              # Ingress + lifespan wiring
│   └── webhooks.py         # HMAC signature verification
├── skills/                # Event processing skills
│   ├── pr_summary.py       # PR review summary (write)
│   ├── issue_label.py      # Issue labeling (write)
│   ├── issue_response.py   # Issue response (write)
│   ├── pr_triage.py        # PR triage (read-only)
│   ├── issue_triage.py     # Issue triage (read-only)
│   ├── decisions.py        # Typed LLM output parsing
│   ├── payload.py          # Webhook payload extraction
│   └── base.py             # Skill framework
├── prompts/               # LLM prompt templates (.md)
├── providers/             # LLM provider adapters
│   ├── openai.py, anthropic.py, grok.py, ollama.py
│   └── base.py
└── tools/                 # Helper integrations

config/models.yaml          # Model catalog
tests/                      # 211 tests (unit, skill, orchestrator, integration)
```

## Engineering and safety contract

**Constraints:**
- Python ≥3.12, mypy strict, ruff, pytest
- Frozen dataclasses for all value objects
- Async architecture for all runtime paths
- Phased execution: one capability group at a time, tests first

**Safety requirements:**
- Webhook signature verification before processing
- Idempotency before any GitHub write (delivery ID + action fingerprint)
- Mandatory dry-run mode before enabling writes
- Allowlist gating before skill execution
- Action fault isolation (one failure does not stop others)
- No secrets in logs
