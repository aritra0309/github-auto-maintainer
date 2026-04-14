# Security Model

GitHub Auto-Maintainer follows a **defence-in-depth** approach — multiple independent layers must all agree before any write action reaches the GitHub API.

---

## Webhook Signature Verification

Every incoming request to `POST /webhook` is verified using **HMAC SHA-256**:

- The `X-Hub-Signature-256` header must be present and valid.
- The signature is computed over the raw request body using `GITHUB_WEBHOOK_SECRET` as the key.
- **Unsigned or incorrectly signed requests are rejected immediately** with HTTP 403.

This ensures only GitHub (or someone with your webhook secret) can trigger the system.

---

## GitHub App Authentication

Authentication uses the **GitHub App model** with short-lived tokens:

1. A **JWT** is generated from the App's private key (10-minute expiry).
2. The JWT is exchanged for an **installation access token** (1-hour TTL).
3. All GitHub API calls use the installation token.
4. **No long-lived tokens are stored** — tokens are generated on demand and expire automatically.

The private key (`.pem` file) is read from the path specified in `GITHUB_APP_PRIVATE_KEY_PATH` and never logged or serialised.

---

## DRY_RUN Default

`DRY_RUN=true` is the **default** setting. When enabled:

- The full pipeline runs (event normalisation → skill routing → action planning).
- All planned write actions are **logged but not executed**.
- You must explicitly set `DRY_RUN=false` to enable actual GitHub writes.

This prevents accidental writes during development, testing, or initial deployment.

---

## Repository Allowlist

The `GITHUB_ALLOWED_REPOSITORIES` environment variable gates **all write actions**:

- Set to a comma-separated list of `owner/repo` values (e.g. `acme/api,acme/web`).
- Requests targeting repositories **not on the list are dropped** before skill execution.
- An **empty allowlist permits all repositories** — useful for development but not recommended for production.

```bash
GITHUB_ALLOWED_REPOSITORIES=myorg/backend,myorg/frontend
```

---

## Event Allowlist

The `GITHUB_ALLOWED_EVENTS` environment variable gates which **event types** are processed:

- Set to a comma-separated list of event names (e.g. `issues,pull_request`).
- Events not on the list are **silently dropped**.
- An **empty allowlist permits all events**.

```bash
GITHUB_ALLOWED_EVENTS=issues,issue_comment,pull_request,push
```

---

## Auto-Fix Safety Guardrails

The auto-fix pipeline (`automation/safety.py`) enforces additional constraints on any code changes the bot proposes:

### Blocked Paths

The following paths **cannot be modified** by the auto-fix pipeline:

| Pattern | Reason |
|---|---|
| `.github/workflows/` | CI/CD pipeline definitions |
| `.github/actions/` | Custom GitHub Actions |
| `.env` | Secrets / local configuration |
| `*.pem` | Private key files |
| `*.key` | Private key files |
| `secrets/` | Secrets directory |
| `.secrets/` | Secrets directory |

### Blocked Extensions

Files with these extensions are always rejected: `.pem`, `.key`, `.p12`, `.pfx`, `.jks`

### Diff Size Limits

| Limit | Default | Description |
|---|---|---|
| Max diff lines | 500 | Total lines across all changed files |
| Max files changed | 10 | Number of files in a single patch |
| Max single file lines | 200 | Lines changed in any one file |

Patches exceeding these limits are rejected to prevent runaway changes.

### Path Traversal Rejection

Any file path containing `..` (parent directory traversal) is **rejected immediately**, preventing the bot from writing outside the repository root.

### Allowed Commands

Only explicitly whitelisted commands can be executed:

| Command | Template | Timeout |
|---|---|---|
| `ruff` | `ruff check --fix .` | 60s |
| `mypy` | `mypy .` | 120s |
| `pytest` | `pytest -x -q` | 300s |

Any command not in this list raises a `SafetyError`.

---

## Non-Root Container

The Dockerfile creates and switches to a dedicated `appuser` account:

```dockerfile
RUN useradd --create-home --shell /bin/bash appuser
USER appuser
```

The application **never runs as root** inside the container, limiting the blast radius of any container escape.

---

## No Secrets in Logs

A structlog **redaction processor** runs on every log entry:

- **Key-based redaction**: any field whose name matches `token`, `api_key`, `authorization`, `bearer`, `private_key`, `secret`, or `password` is replaced with `[REDACTED]`.
- **Value-based redaction**: free-text fields are scanned for patterns matching:
  - `Bearer <token>` headers
  - `-----BEGIN PRIVATE KEY-----` blocks
  - GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`)
  - LLM API keys (`sk-...`)

This applies recursively to nested mappings and sequences.

---

## Idempotency

Webhook replays are handled via **delivery ID + action fingerprint** deduplication:

- Each action is keyed by `{delivery_id}::{action_fingerprint}`.
- If the same key has been seen before, the write is **skipped**.
- This prevents duplicate comments, labels, or PRs when GitHub retries a webhook delivery.

The default implementation is in-memory (`InMemoryIdempotencyStore`); the interface is protocol-based and can be swapped for a persistent store.
