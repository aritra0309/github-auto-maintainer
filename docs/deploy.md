# Deployment Guide

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.12+ | Required for local development |
| Docker | 20.10+ | Optional — for containerised deployment |
| GitHub App | — | [Create one →](https://docs.github.com/en/apps/creating-github-apps) |
| LLM API key | — | At least one provider (Anthropic, OpenAI, Google, xAI, OpenRouter, NVIDIA NIM, or local Ollama) |

---

## GitHub App Setup

1. **Create a GitHub App** at <https://github.com/settings/apps/new>.
2. **Permissions** — grant the following repository permissions:
   - Issues: **Read & Write**
   - Pull requests: **Read & Write**
   - Contents: **Read & Write**
   - Metadata: **Read**
3. **Subscribe to events**:
   - `issues`
   - `issue_comment`
   - `pull_request`
   - `push`
4. **Webhook URL**: set to `https://<your-host>/webhook` (see [Webhook Endpoint](#webhook-endpoint) below).
5. **Webhook secret**: generate a strong random string and save it — you'll need it for `GITHUB_WEBHOOK_SECRET`.
6. **Generate a private key** — download the `.pem` file and note its path.
7. **Note your App ID** (shown on the app's settings page) and **Installation ID** (visible in the URL after installing the app on a repo: `https://github.com/settings/installations/<ID>`).

---

## Configuration

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

### Required variables

| Variable | Description |
|---|---|
| `GITHUB_APP_ID` | Your GitHub App's numeric ID |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Path to the `.pem` private key file |
| `GITHUB_WEBHOOK_SECRET` | Webhook secret configured in the GitHub App |
| `GITHUB_ALLOWED_REPOSITORIES` | Comma-separated `owner/repo` list (e.g. `acme/api,acme/web`). Empty = allow all. |
| `DRY_RUN` | `true` (default) — set to `false` to enable actual GitHub writes |

### LLM provider keys (at least one required)

| Variable | Provider |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (Claude) |
| `OPENAI_API_KEY` | OpenAI |
| `GOOGLE_API_KEY` | Google / Gemini |
| `XAI_API_KEY` | xAI / Grok |
| `OPENROUTER_API_KEY` | OpenRouter |
| `NVIDIA_NIM_API_KEY` | NVIDIA NIM |
| `OLLAMA_API_BASE` | Ollama (default `http://localhost:11434`) |

### Optional variables

| Variable | Default | Description |
|---|---|---|
| `GITHUB_ALLOWED_EVENTS` | *(empty = all)* | Comma-separated event types to process |
| `DEFAULT_PROVIDER` | *(auto)* | Preferred LLM provider |
| `DEFAULT_MODEL` | *(auto)* | Preferred LLM model |
| `AUTO_FIX_ENABLED` | `true` | Enable the auto-fix pipeline |
| `AUTO_FIX_TRIGGER_LABEL` | `auto-fix` | Issue label that triggers auto-fix |
| `AUTO_FIX_TRIGGER_COMMAND` | `/auto-fix` | Comment command that triggers auto-fix |
| `RUN_STORE_PATH` | `runs.db` | SQLite database path for run metadata |
| `LOG_FORMAT` | `json` | `json` for production, `dev` for coloured console |

---

## Local Development

```bash
# Install in editable mode with dev dependencies
python -m pip install -e '.[dev]'

# Copy and edit configuration
cp .env.example .env
# → fill in GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH, GITHUB_WEBHOOK_SECRET, etc.

# Start the server
make run

# In another terminal — expose localhost for GitHub webhook delivery
ngrok http 8000
# Copy the ngrok HTTPS URL and set it as your GitHub App's webhook URL:
#   https://<random>.ngrok-free.app/webhook
```

### Local with Ollama (no cloud LLM)

```bash
make run-local
# Equivalent to: DEFAULT_PROVIDER=ollama python -m github_auto_maintainer
```

---

## Docker Deployment

```bash
# Build and start (detached)
docker compose up --build -d

# Follow logs
docker compose logs -f

# Stop
docker compose down
```

The Docker Compose setup:
- Reads `.env` for all configuration
- Persists SQLite data in a named volume (`sqlite_data` → `/app/data/runs.db`)
- Runs as non-root user (`appuser`)
- Includes a built-in health check (every 30s)
- Restarts automatically (`unless-stopped`)

### Standalone Docker (without Compose)

```bash
make docker-build
make docker-run
# Or manually:
# docker build -t github-auto-maintainer .
# docker run -d -p 8000:8000 --env-file .env -v gham-data:/app/data --name gham github-auto-maintainer
```

---

## Health Check

```bash
curl http://localhost:8000/health
```

Returns HTTP 200 when the service is running and ready.

---

## Webhook Endpoint

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook` | Receives GitHub webhook events |

Configure this URL in your GitHub App settings → Webhook URL:

```
https://<your-host>/webhook
```

All incoming requests are verified against `GITHUB_WEBHOOK_SECRET` using HMAC SHA-256 (see [Security Model](security.md)).

---

## DRY_RUN Mode

By default, `DRY_RUN=true` — the system processes events and runs the full pipeline but **skips all GitHub write operations** (comments, labels, branches, PRs).

To enable writes:

```bash
DRY_RUN=false
```

This is a deliberate safety default. See [Security Model](security.md) for the full defence-in-depth story.

---

## Make Targets

| Target | Description |
|---|---|
| `make test` | Run the test suite (`pytest`) |
| `make lint` | Run linters (`ruff check` + `mypy`) |
| `make run` | Start the server locally |
| `make run-local` | Start with Ollama as default provider |
| `make docker-build` | Build the Docker image |
| `make docker-run` | Run container standalone |
| `make docker-up` | `docker compose up -d --build` |
| `make docker-down` | `docker compose down` |
