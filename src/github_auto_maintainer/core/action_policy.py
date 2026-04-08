"""Action policy: DRY_RUN mode and repo/event allowlists."""

from __future__ import annotations

import os


class ActionPolicy:
    """Gates write actions behind DRY_RUN mode and allowlists.

    Constructor kwargs override env vars, enabling deterministic testing.
    """

    def __init__(
        self,
        *,
        dry_run: bool | None = None,
        allowed_repositories: tuple[str, ...] | None = None,
        allowed_events: tuple[str, ...] | None = None,
    ) -> None:
        if dry_run is not None:
            self._dry_run = dry_run
        else:
            env_val = os.getenv("DRY_RUN", "true").strip().lower()
            self._dry_run = env_val != "false"

        if allowed_repositories is not None:
            self._allowed_repos = allowed_repositories
        else:
            raw = os.getenv("GITHUB_ALLOWED_REPOSITORIES", "").strip()
            self._allowed_repos = (
                tuple(r.strip() for r in raw.split(",") if r.strip()) if raw else ()
            )

        if allowed_events is not None:
            self._allowed_events = allowed_events
        else:
            raw = os.getenv("GITHUB_ALLOWED_EVENTS", "").strip()
            self._allowed_events = (
                tuple(e.strip() for e in raw.split(",") if e.strip()) if raw else ()
            )

    @property
    def dry_run(self) -> bool:
        """True means no writes will be executed."""
        return self._dry_run

    def is_repo_allowed(self, repo_full_name: str | None) -> bool:
        """Check if a repository is allowed. Empty allowlist = allow all."""
        if not self._allowed_repos:
            return True
        if repo_full_name is None:
            return False
        return repo_full_name in self._allowed_repos

    def is_event_allowed(self, event_name: str) -> bool:
        """Check if an event type is allowed. Empty allowlist = allow all."""
        if not self._allowed_events:
            return True
        return event_name in self._allowed_events
