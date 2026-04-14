"""CLI entrypoint for github_auto_maintainer."""

from __future__ import annotations

import argparse
import sys

import uvicorn

from github_auto_maintainer.core.errors import LLMRouterError
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.logging_config import configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-maintainer",
        description="GitHub Auto-Maintainer — policy-driven repository automation",
    )
    sub = parser.add_subparsers(dest="command")

    # Default: serve mode (also the behaviour when no subcommand is given)
    sub.add_parser("serve", help="Start the webhook ingress server (default)")

    # Single-shot: process a single event from a JSON file
    pe = sub.add_parser(
        "process-event",
        help="Process a single GitHub event from a JSON file, then exit",
    )
    pe.add_argument(
        "--event-path",
        required=True,
        help="Path to the GitHub event JSON file (typically $GITHUB_EVENT_PATH)",
    )

    return parser


def _serve() -> None:
    """Start the webhook ingress server (original behaviour)."""
    config = RouterConfig.from_env()
    router = LLMRouter(config=config)

    try:
        router.validate_startup()
    except LLMRouterError as exc:
        print(
            "github-auto-maintainer startup validation failed. "
            "Check that at least one LLM provider API key is set. "
            f"Details: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    provider_info = (
        f"provider={config.default_provider}" if config.default_provider else "provider=auto"
    )
    model_info = f"model={config.default_model}" if config.default_model else "model=auto"
    print(f"github-auto-maintainer bootstrap: {provider_info} {model_info} status=validated")

    uvicorn.run(
        "github_auto_maintainer.server.app:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )


def main() -> None:
    configure_logging()

    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "process-event":
        from github_auto_maintainer.cli import process_event

        process_event(args.event_path)
    else:
        # Default: serve mode (handles both `serve` subcommand and no subcommand)
        _serve()


if __name__ == "__main__":
    main()
