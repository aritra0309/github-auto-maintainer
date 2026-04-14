# Operations Runbook

## Monitoring

### Structured Logging

All logs are emitted as **structured JSON** to stdout (default). Each log entry includes contextual fields bound via structlog contextvars.

### Key Log Events

| Event | Fields | What it tells you |
|---|---|---|
| `http.request_completed` | `method`, `path`, `status_code`, `latency_ms` | Request-level latency and status |
| `llm.response_received` | `provider`, `model`, `latency_ms`, `escalation_count` | LLM call performance and escalation chain depth |
| `skill.executed` | `skill`, `event_type`, `delivery_id` | Which skill handled which event |
| `action.executed` | `action_type`, `repo`, `dry_run` | Write actions taken (or skipped in dry-run) |

### Request Tracing

Every request is assigned a unique `request_id` (UUID v4) and the GitHub `delivery_id` is bound to all log entries for that request. The `X-Request-ID` response header echoes the request ID for correlation.

---

## Log Format

| `LOG_FORMAT` | Output | Use case |
|---|---|---|
| `json` (default) | JSON lines to stdout | Production — pipe to log aggregator |
| `dev` | Coloured, human-readable console | Local development |

```bash
# Production (default)
LOG_FORMAT=json

# Development
LOG_FORMAT=dev
```

---

## SQLite Persistence

| Variable | Default | Description |
|---|---|---|
| `RUN_STORE_PATH` | `runs.db` | Path to SQLite database |
| `SQLITE_DB_PATH` | `/app/data/runs.db` | Docker override (via compose) |

The SQLite database stores **auto-fix run metadata** — run ID, status, timestamps, associated issue/PR, and error details.

### Docker Volume

The `docker-compose.yml` mounts a named volume:

```yaml
volumes:
  sqlite_data:  # → /app/data inside the container
```

This persists run data across container restarts. To reset, remove the volume:

```bash
docker compose down -v
```

---

## Scaling

The application runs as a **single uvicorn process** with an **in-memory job queue** (`InMemoryJobQueue`).

This is sufficient for low-to-moderate webhook volume. For higher throughput:

1. **Replace the queue**: the `JobQueue` interface is protocol-based — swap `InMemoryJobQueue` for a Redis/Celery-backed implementation.
2. **Run multiple workers**: with an external queue, multiple app instances can consume jobs concurrently.
3. **Replace the idempotency store**: swap `InMemoryIdempotencyStore` for a Redis or database-backed store to share state across workers.

---

## Upgrading

### Docker Compose

```bash
# Pull latest image (if using a registry) and rebuild
docker compose pull
docker compose up --build -d
```

### Local

```bash
git pull
python -m pip install -e '.[dev]'
make run
```

The SQLite schema is forward-compatible — new columns are added with defaults and never removed.

---

## Common Issues

### Webhook returns 403

**Cause**: Webhook signature verification failed.

**Fix**: Ensure `GITHUB_WEBHOOK_SECRET` in your `.env` matches the secret configured in your GitHub App's webhook settings. The values must be identical — no trailing whitespace or newlines.

---

### No actions taken on events

**Cause**: Writes are disabled or the repo isn't on the allowlist.

**Checklist**:
1. Set `DRY_RUN=false` in `.env`.
2. Ensure `GITHUB_ALLOWED_REPOSITORIES` includes your repo (e.g. `myorg/myrepo`).
3. If `GITHUB_ALLOWED_EVENTS` is set, ensure it includes the relevant event type.
4. Check logs for `action.executed` entries with `dry_run=true`.

---

### LLM errors / no response

**Cause**: Missing or invalid provider API key.

**Fix**: Ensure at least one LLM provider key is set:

| Variable | Provider |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (Claude) |
| `OPENAI_API_KEY` | OpenAI |
| `GOOGLE_API_KEY` | Google / Gemini |
| `XAI_API_KEY` | xAI / Grok |
| `OPENROUTER_API_KEY` | OpenRouter |
| `NVIDIA_NIM_API_KEY` | NVIDIA NIM |
| `OLLAMA_API_BASE` | Ollama (local) |

Check logs for error entries with `provider` and `error_class` fields.

---

### Auto-fix not triggering

**Cause**: Pipeline is disabled or trigger conditions aren't met.

**Checklist**:
1. Set `AUTO_FIX_ENABLED=true` (default).
2. Ensure the issue has the `auto-fix` label (configurable via `AUTO_FIX_TRIGGER_LABEL`).
3. Or ensure a comment contains `/auto-fix` (configurable via `AUTO_FIX_TRIGGER_COMMAND`).
4. Verify the repo is on the allowlist and `DRY_RUN=false`.

---

### Container health check failing

**Cause**: The app hasn't started or crashed.

**Fix**:
```bash
# Check container status
docker compose ps

# Check logs for startup errors
docker compose logs app | head -50

# Test health manually
curl http://localhost:8000/health
```

---

## Make Targets

| Target | Command | Description |
|---|---|---|
| `make test` | `python -m pytest` | Run the full test suite |
| `make lint` | `ruff check . && mypy .` | Run linters and type checker |
| `make run` | `PYTHONPATH=src python -m github_auto_maintainer` | Start the server |
| `make run-local` | `DEFAULT_PROVIDER=ollama PYTHONPATH=src python -m github_auto_maintainer` | Start with Ollama |
| `make docker-build` | `docker build -t github-auto-maintainer .` | Build Docker image |
| `make docker-run` | `docker run -d -p 8000:8000 ...` | Run container standalone |
| `make docker-up` | `docker compose up -d --build` | Start via Compose |
| `make docker-down` | `docker compose down` | Stop via Compose |
