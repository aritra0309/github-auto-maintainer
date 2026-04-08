"""CLI entrypoint for github_auto_maintainer."""

from __future__ import annotations

import sys

import uvicorn

from github_auto_maintainer.core.errors import LLMRouterError
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig


def main() -> None:
    config = RouterConfig.from_env()
    router = LLMRouter(config=config)

    try:
        router.validate_startup()
    except LLMRouterError as exc:
        print(
            "github-auto-maintainer startup validation failed. "
            "Check DEFAULT_PROVIDER, DEFAULT_MODEL, and MODEL_CATALOG_PATH. "
            f"Details: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    print(
        "github-auto-maintainer bootstrap: "
        f"provider={config.default_provider} model={config.default_model} status=validated"
    )

    uvicorn.run(
        "github_auto_maintainer.server.app:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
