# GitHub Auto-Maintainer

GitHub Auto-Maintainer is a policy-driven automation project for repository maintenance. It is built to reduce repetitive maintainer work such as triage, routing, summarization, and follow-up actions while keeping behavior deterministic, testable, and safe.

This repository is being built as an engineering-first system: strict typing, deterministic model selection, explicit safety gates, and clear separation between policy and execution.

## Why this project exists

Maintainers spend substantial time on repeatable but important work: understanding incoming issues and pull requests, prioritizing what to review first, and keeping discussions actionable. AI can help, but in real engineering teams the bigger challenge is not raw generation quality; it is reliability, auditability, and operational control.

This project focuses on that control layer.

- predictable routing decisions,
- provider and model portability,
- explicit escalation paths,
- safe-by-default runtime behavior.

## What is implemented as of now

The deterministic routing foundation is complete.

Core additions and updates:

- `src/github_auto_maintainer/core/settings.py` provides typed runtime settings via `pydantic-settings`.
- `src/github_auto_maintainer/core/model_catalog.py` loads and validates `config/models.yaml` into typed descriptors.
- `src/github_auto_maintainer/core/task_types.py` defines `TaskType` and `TaskComplexity` enums.
- `src/github_auto_maintainer/core/routing_policy.py` implements deterministic model ranking/selection and fixed escalation order.
- `src/github_auto_maintainer/core/llm_router.py` now supports:
  - `complete_task(...)` for task-intent-based routing,
  - `complete_with_escalation(...)` for deterministic tier escalation.
- `.env.example` and defaults are aligned to:
  - `DEFAULT_PROVIDER=openai`
  - `DEFAULT_MODEL=gpt-5.4-mini`

Quality and validation coverage in `tests/` includes catalog parsing failures, routing behavior, escalation scenarios with fake providers/validators, and startup validation checks.

Current runtime scope:

- The CLI (`src/github_auto_maintainer/__main__.py`) is currently bootstrap-oriented.
- Live webhook ingestion and GitHub App event handling are not implemented yet.

## How this differs from Copilot PR review

Copilot PR review is a hosted review assistant focused on PR feedback quality. GitHub Auto-Maintainer targets a different layer: repository operations and automation control.

Practical differences:

- This project is designed as an automation backbone around GitHub events, not only a PR comment generator.
- Routing is deterministic and policy-based; model choice is not delegated to an LLM.
- Provider/model selection is typed and configurable across backends (`openai`, `anthropic`, `grok`, `ollama`).
- The architecture is designed for strict guardrails (signature verification, idempotency, dry-run controls) before write actions.

In short, Copilot can assist review content; this project aims to run a safe, auditable maintenance pipeline.

## What we are building next

The next stage is converting this routing core into a real GitHub event runtime.

First, the system will add secure webhook ingress with GitHub App authentication, event normalization, and a queue boundary. Incoming events should be verified and enqueued quickly, with heavier reasoning handled asynchronously.

Then the project will add a typed GitHub REST client, diff parsing, and read-only triage skills for pull requests and issues. That stage is intentionally non-mutating so behavior can be evaluated on real payloads without risk.

After read-only behavior stabilizes, write actions will be introduced with hard safeguards: idempotency, dry-run mode, and repository/event allowlists. Initial write actions will focus on low-risk value such as summary comments and labels.

Only after those controls are proven will controlled auto-fix workflows be added (generate patch, run checks, open PR) with strict safety constraints and human approval required for merge.

Deployment, observability, and runbooks will follow so the system is reproducible, operable, and understandable by new users.

## Engineering and safety contract

Project constraints:

- Python `>=3.12`
- quality gates: `ruff`, `mypy` (strict), `pytest`
- asynchronous architecture for runtime paths
- phased execution: one capability group at a time, with tests first

Safety requirements for operational flows:

- webhook signature verification before processing,
- idempotency before any GitHub write action,
- mandatory `DRY_RUN` before enabling writes,
- no secrets in logs.

## Run locally

1. Create and activate a Python environment.
2. Install dependencies:

```bash
python -m pip install -e '.[dev]'
```

3. Copy the env template and set values:

```bash
cp .env.example .env
```

Default router settings:

- `DEFAULT_PROVIDER=openai`
- `DEFAULT_MODEL=gpt-5.4-mini`
- `MODEL_CATALOG_PATH` optional override (defaults to `config/models.yaml`)

4. Run checks:

```bash
make lint
make test
```

5. Run bootstrap CLI validation:

```bash
make run
```

For local/offline model development with Ollama:

```bash
make run-local
```

## Repository layout

- `src/github_auto_maintainer/core/` runtime core (routing, settings, policy, hooks)
- `src/github_auto_maintainer/providers/` provider adapters
- `src/github_auto_maintainer/skills/` skill modules
- `src/github_auto_maintainer/tools/` helper integrations
- `config/models.yaml` model catalog used by routing policy
- `tests/` unit and integration tests

## Current maturity snapshot

Today this repository is a strong deterministic foundation with test coverage. The GitHub event runtime and write-capable automation layers are the next major capabilities being built.
