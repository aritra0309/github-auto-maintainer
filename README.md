# Github Auto-Maintainer

Automated GitHub maintenance agent that reviews pull requests, responds to issue comments, and helps keep repositories healthy through policy-driven checks.

## What It Does

- Listens to GitHub App webhook events.
- Analyzes PRs and issue discussions.
- Posts review comments, summaries, and follow-up actions.
- Supports pluggable AI providers and skill modules.

## Run Locally

1. Create and activate a Python environment.
2. Install dependencies:

```bash
pip install -e .[dev]
```

3. Copy the env template and fill values:

```bash
cp .env.example .env
```

4. Run tests and checks:

```bash
ruff check .
mypy .
pytest
```

## Add A New Provider

1. Implement a provider class under `src/github_auto_maintainer/providers/`.
2. Match the provider interface used by the dispatcher.
3. Register the provider in the provider registry.
4. Add provider-specific env vars in `.env.example`.
5. Add tests for success and failure paths.
