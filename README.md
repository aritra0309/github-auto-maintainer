# GitHub Auto-Maintainer

A policy-driven automation system for GitHub repository maintenance. It reduces repetitive maintainer work — triage, routing, summarization, labeling, and follow-up — while keeping behavior deterministic, testable, and safe.

Built with strict typing, deterministic model selection, explicit safety gates, and clear separation between policy and execution.

## Why this project exists

Maintainers spend substantial time on repeatable but important work: understanding incoming issues and pull requests, prioritizing what to review first, and keeping discussions actionable. AI can help, but in real engineering teams the bigger challenge is not raw generation quality — it is reliability, auditability, and operational control.

This project focuses on that control layer:

- Predictable, policy-driven routing decisions
- Provider and model portability (OpenAI, Anthropic, Google Gemini, Grok, Ollama, OpenRouter, NVIDIA NIM)
- Explicit escalation paths when models fail to produce valid output
- Safe-by-default runtime behavior with mandatory dry-run mode

## How it differs from Copilot, Claude, or Codex-style assistants

Tools like Copilot, Claude, and Codex help a developer in the moment — writing code, reviewing changes, suggesting fixes. GitHub Auto-Maintainer solves a different problem: **what happens around the code**, not inside a file.

It watches the repository's event stream (issues opened, PRs submitted, labels applied, comments posted), decides what kind of work each event represents, routes it to the right model tier, and takes safe, idempotent write actions — or opens a patch PR — without a human in the loop.

The design goal is not to replace human judgment. It is to eliminate the mechanical parts of maintainership so humans can focus on the parts that actually require judgment.


## Getting started

### 1. Create a GitHub App

Go to **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**.

| Field | Value |
|---|---|
| Homepage URL | Any URL (e.g. your repo URL) |
| Webhook URL | Your server's `/webhook` endpoint (see step 4) |
| Webhook secret | Generate a random secret — you'll put this in `.env` |
| Active | ✅ |

**Repository permissions (read & write):**

| Permission | Access |
|---|---|
| Contents | Read & write (needed for branch/commit in auto-fix) |
| Issues | Read & write |
| Pull requests | Read & write |
| Metadata | Read-only (required) |

**Subscribe to events:**

- `Issues`
- `Issue comment`
- `Pull request`
- `Pull request review`

Click **Create GitHub App**. On the next page:

- Note your **App ID** (shown at the top).
- Scroll to **Private keys** → **Generate a private key**. Save the downloaded `.pem` file.

---

### 2. Install the App on a repository

Go to your new App's page → **Install App** → select the repo(s) you want it to manage.

---

### 3. Configure `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in:

```env
# GitHub App credentials
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
<paste the full contents of your .pem file here>
-----END RSA PRIVATE KEY-----"
GITHUB_WEBHOOK_SECRET=your-webhook-secret

# At least one LLM provider key
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
# GOOGLE_API_KEY=...

# Safety: restrict which repos the bot can act on
GITHUB_ALLOWED_REPOSITORIES=owner/repo,owner/other-repo

# Start in dry-run mode (default). No writes will happen until you set this to false.
DRY_RUN=true

# Optional: enable the auto-fix pipeline
AUTO_FIX_ENABLED=false

# Optional: coloured console logs for local dev
LOG_FORMAT=dev
```

---

### 4. Start the server

**Local dev (Python):**

```bash
python -m pip install -e '.[dev]'
make run
# Server starts on http://localhost:8000
```

**Local dev (Docker):**

```bash
docker compose up
# Server starts on http://localhost:8000
```

---

### 5. Expose your local server to GitHub (local dev only)

GitHub needs a public URL to deliver webhooks. Use [ngrok](https://ngrok.com):

```bash
ngrok http 8000
# Outputs something like: https://abc123.ngrok-free.app
```

Go back to your GitHub App settings → **Webhook URL** → update it to:

```
https://abc123.ngrok-free.app/webhook
```

---

### 6. Verify the connection

Open an issue or PR in your installed repo. You should see:

- A webhook delivery appear under your GitHub App → **Advanced → Recent Deliveries**
- A log line in your server output showing the event was received and processed
- In dry-run mode: log output showing what *would* have been written, but no actual GitHub actions

---

### 7. Enable write actions when ready

Once you're satisfied with the dry-run output:

```env
DRY_RUN=false
GITHUB_ALLOWED_REPOSITORIES=owner/repo   # keep this set
```

Restart the server. The bot will now post comments, add labels, open PR reviews, and (if `AUTO_FIX_ENABLED=true`) open patch PRs.

---

### Production deployment

See [`docs/deploy.md`](docs/deploy.md) for Docker, GitHub Actions workflow mode (serverless), and bare-metal deployment. See [`docs/security.md`](docs/security.md) for the full security model.

## What is implemented

### Phase 1 — Deterministic Routing + Typed Settings ✅

Auto-discovery model catalog (`core/model_catalog.py`) that detects available providers from API keys, scans LiteLLM's live model registry, computes 6-tier cost bucketing from real pricing data, and auto-assigns task types. Typed settings, task types, routing policy, LLM router with `complete()` / `complete_task()` / `complete_with_escalation()`, hook bus for prompt/response observability, and a unified LiteLLM provider adapter supporting 100+ backends.

### Phase 2 — Webhook Ingress + GitHub App Auth + Queue ✅

FastAPI ingress with `/health` and `/webhook` endpoints. Webhook signature verification via HMAC SHA-256. GitHub App JWT generation and installation token retrieval. Event normalization into typed `NormalizedEvent` envelopes. In-memory async job queue abstraction, swappable for Redis or Celery.

### Phase 3 — GitHub Client + Diff Parser + Read-Only Skills ✅

Async read-only GitHub REST client with typed return objects, pagination, and error mapping. Pure unified diff parser handling binary, rename, add, delete, and truncated diffs. Skill framework with `SkillContext`, generic `SkillResult[T]`, and `BaseSkill` ABC. Typed decision parsing with `from_llm_response()`. Two read-only skills: `PRTriageSkill` and `IssueTriageSkill`. No write operations in this phase.

### Phase 4 — Orchestrator + First Write Actions (Idempotent) ✅

`ActionRequest` protocol with content-aware SHA-256 fingerprints for deduplication. Three write action types: `IssueCommentAction`, `AddLabelsAction`, `PRReviewSummaryAction`. Idempotency layer keyed on delivery ID + action fingerprint. `ActionPolicy` with `DRY_RUN` mode (default `true`), repo allowlist, and event allowlist. Three write-capable skills: `PRSummarySkill`, `IssueLabelSkill`, `IssueResponseSkill`. Full orchestrator replacing the Phase 3 dispatcher.

### Phase 5 — Controlled Auto-Fix Pipeline (Branch/Commit/PR) ✅

`AutoFixSkill` triggered by `issues.labeled` (`auto-fix`) or `issue_comment.created` (`/auto-fix`). Pipeline: fetch issue → LLM patch generation → safety validation → branch/commit/PR via REST API → follow-up comment with PR link. Safety module blocks dangerous paths, extensions, and oversized diffs. All git operations use the GitHub REST API — no subprocess git, no `shell=True`. SQLite-backed run metadata persistence via `aiosqlite`. Three new action types: `CreateBranchAction`, `CommitPatchAction`, `CreatePullRequestAction`.

### Phase 6 — Deployment + Observability + Portfolio Polish ✅

Structured logging via structlog — JSON in production, coloured console in dev. Secret redaction on all log output. `escalation_count` field on `LLMResponse`. `LoggingHookSubscriber` wired to the hook bus. `RequestTimingMiddleware` logging per-request latency and injecting `X-Request-ID`. Multi-stage Dockerfile with non-root user, health check, and SQLite volume. `docker-compose.yml` for one-command local deployment. Deployment, security, and ops docs. GitHub Actions workflow mode with a single-shot CLI for serverless event processing. Resilience tests (duplicate delivery, LLM outage, GitHub 429), container smoke tests, and CLI tests.

## End-to-end request flow

```
GitHub webhook
  → FastAPI ingress (server/app.py)
  → HMAC signature verification
  → Event normalization (NormalizedEvent)
  → Async job queue
  → Orchestrator
      → Allowlist check (repo + event type)
      → Match event to skills
      → Generate GitHub App JWT → fetch installation token → create GitHubClient
      → Execute matching skills, collect planned_actions
      → Execute actions with idempotency check + dry-run gate
      → Structured JSON log of outcomes

Auto-fix path:
  issues.labeled "auto-fix" OR issue_comment.created "/auto-fix"
      → AutoFixSkill
          → Fetch issue details
          → LLM patch generation
          → Safety validation
          → create branch → commit files → open PR (all via REST API)
          → Follow-up IssueCommentAction with PR link
          → Run metadata persisted to SQLite
```

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

### Docker (one command)

```bash
docker compose up
```

See `docs/deploy.md` for full deployment instructions, `docs/security.md` for the security model, and `docs/ops.md` for operational runbook.

### Key environment variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_APP_ID` | Yes | GitHub App ID |
| `GITHUB_APP_PRIVATE_KEY` | Yes | PEM-encoded private key |
| `GITHUB_WEBHOOK_SECRET` | Yes | Webhook secret for HMAC verification |
| `ANTHROPIC_API_KEY` | At least one | LLM provider key |
| `OPENAI_API_KEY` | At least one | LLM provider key |
| `GOOGLE_API_KEY` | At least one | LLM provider key |
| `GITHUB_ALLOWED_REPOSITORIES` | Recommended | Comma-separated `owner/repo` allowlist |
| `DRY_RUN` | — | Default `true`. Set `false` to enable write actions |
| `AUTO_FIX_ENABLED` | — | Default `false`. Set `true` to enable the auto-fix pipeline |
| `LOG_FORMAT` | — | `json` (default) or `dev` for coloured console output |

## Repository layout

```
src/github_auto_maintainer/
├── core/           # Router, catalog, orchestrator, actions, idempotency, queue, logging
├── github/         # REST client, auth, events, diff parser, retry, errors
├── skills/         # Skill framework, decision types, all skill implementations
├── automation/     # Auto-fix pipeline, safety, git ops, check runner
├── server/         # FastAPI app, webhook handler, middleware
├── providers/      # LiteLLM adapter
├── prompts/        # Prompt templates (loaded via importlib.resources)
└── cli.py          # Single-shot CLI for GitHub Actions workflow mode

tests/
├── core/           # Unit tests for router, catalog, orchestrator, actions
├── github/         # Unit tests for client, auth, diff parser
├── skills/         # Unit tests for all skills and decision types
├── automation/     # Unit tests for auto-fix pipeline and safety
├── integration/    # End-to-end happy path, dry-run, idempotency, auto-fix
├── resilience/     # Chaos tests: duplicate delivery, LLM outage, GitHub 429
└── smoke/          # Container smoke tests (requires Docker)

docs/
├── deploy.md       # Deployment guide (Docker, GitHub Actions, bare metal)
├── security.md     # Security model and threat surface
└── ops.md          # Operational runbook
```

## Engineering and safety contract

- **Routing is deterministic.** Model selection is never delegated to an LLM.
- **Dry-run by default.** `DRY_RUN=true` is the default. Write actions require an explicit opt-in.
- **Allowlist gating.** Every event is checked against the repo and event allowlists before any skill runs.
- **Idempotency on all writes.** Every GitHub write action is keyed on delivery ID + content fingerprint. Replays are no-ops.
- **Safety before git.** The auto-fix pipeline validates every patch against blocked paths, extensions, and size limits before any branch or commit is created.
- **No secrets in logs.** All log output passes through a redaction layer before emission.
- **Single provider adapter.** All LLM calls go through one `LiteLLMProvider`. Adding a new provider is env-var-only — set the API key, restart.
- **No subprocess git.** All git operations use the GitHub REST API directly.
