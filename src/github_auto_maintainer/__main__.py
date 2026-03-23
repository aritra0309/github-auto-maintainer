"""CLI entrypoint for github_auto_maintainer."""

from __future__ import annotations

from github_auto_maintainer.core.llm_router import RouterConfig


def main() -> None:
    config = RouterConfig.from_env()
    provider = config.default_provider
    model = config.default_model
    print(f"github-auto-maintainer bootstrap: provider={provider} model={model}")


if __name__ == "__main__":
    main()
