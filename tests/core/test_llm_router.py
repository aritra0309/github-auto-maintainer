from __future__ import annotations

from collections.abc import Sequence

import pytest
from unittest.mock import patch
from tenacity import wait_none

from github_auto_maintainer.core.errors import (
    ModelCatalogLoadError,
    RouterStartupValidationError,
    TransientProviderError,
)
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.hooks import HookBus
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig, RouterRetryConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.task_types import TaskType
from github_auto_maintainer.providers.base import BaseLLMProvider


class FakeProvider(BaseLLMProvider):
    def __init__(self, provider_name: str, model: str) -> None:
        self.provider_name = provider_name
        self.model = model

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        _ = (system, messages, max_tokens, temperature)
        return LLMResponse(
            content="ok",
            provider=self.provider_name,
            model=self.model,
            input_tokens=11,
            output_tokens=7,
        )


class FlakyProvider(BaseLLMProvider):
    def __init__(self, provider_name: str, model: str) -> None:
        self.provider_name = provider_name
        self.model = model
        self.calls = 0

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        _ = (system, messages, max_tokens, temperature)
        self.calls += 1
        if self.calls < 3:
            raise TransientProviderError("temporary")
        return LLMResponse(
            content="retry-ok",
            provider=self.provider_name,
            model=self.model,
            input_tokens=3,
            output_tokens=2,
        )


def _messages() -> list[LLMMessage]:
    return [{"role": "user", "content": "hello"}]


def _fake_factory(provider: str, model: str, litellm_model: str) -> BaseLLMProvider:
    _ = litellm_model
    return FakeProvider(provider_name=provider, model=model)


@pytest.mark.asyncio
async def test_router_uses_defaults_when_override_not_passed() -> None:
    used: dict[str, str] = {}

    def tracking_factory(provider: str, model: str, litellm_model: str) -> BaseLLMProvider:
        _ = litellm_model
        used["model"] = model
        return FakeProvider(provider_name=provider, model=model)

    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factory=tracking_factory,
    )

    response = await router.complete(
        system="system",
        messages=_messages(),
        max_tokens=32,
        temperature=0.1,
    )

    assert used["model"] == "gpt-default"
    assert response.provider == "openai"
    assert response.model == "gpt-default"


@pytest.mark.asyncio
async def test_router_explicit_provider_model_override_defaults() -> None:
    used: dict[str, str] = {}

    def tracking_factory(provider: str, model: str, litellm_model: str) -> BaseLLMProvider:
        _ = litellm_model
        used["provider"] = provider
        used["model"] = model
        return FakeProvider(provider_name=provider, model=model)

    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factory=tracking_factory,
    )

    response = await router.complete(
        system="sys",
        messages=_messages(),
        max_tokens=16,
        temperature=0.2,
        provider="ollama",
        model="qwen-local",
    )

    assert used == {"provider": "ollama", "model": "qwen-local"}
    assert response.provider == "ollama"
    assert response.model == "qwen-local"


@pytest.mark.asyncio
async def test_router_retries_transient_failures_then_succeeds() -> None:
    flaky = FlakyProvider(provider_name="openai", model="gpt-retry")

    def flaky_factory(provider: str, model: str, litellm_model: str) -> BaseLLMProvider:
        _ = (provider, model, litellm_model)
        return flaky

    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-retry"),
        retry_config=RouterRetryConfig(max_attempts=4, wait_strategy=wait_none()),
        provider_factory=flaky_factory,
    )

    response = await router.complete(
        system="sys",
        messages=_messages(),
        max_tokens=24,
        temperature=0.0,
    )

    assert flaky.calls == 3
    assert response.content == "retry-ok"


@pytest.mark.asyncio
async def test_router_emits_prompt_and_response_hooks_with_token_fields() -> None:
    bus = HookBus()
    prompt_payloads: list[dict[str, object]] = []
    response_payloads: list[dict[str, object]] = []

    async def on_prompt(payload: dict[str, object]) -> None:
        prompt_payloads.append(payload)

    async def on_response(payload: dict[str, object]) -> None:
        response_payloads.append(payload)

    bus.subscribe("on_llm_prompt", on_prompt)
    bus.subscribe("on_llm_response", on_response)

    router = LLMRouter(
        hook_bus=bus,
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factory=_fake_factory,
    )

    await router.complete(
        system="sys",
        messages=_messages(),
        max_tokens=50,
        temperature=0.3,
    )

    assert len(prompt_payloads) == 1
    assert len(response_payloads) == 1
    assert prompt_payloads[0]["provider"] == "openai"
    assert prompt_payloads[0]["model"] == "gpt-default"
    assert prompt_payloads[0]["input_tokens"] == 0
    assert response_payloads[0]["input_tokens"] == 11
    assert response_payloads[0]["output_tokens"] == 7


@pytest.mark.asyncio
async def test_router_supports_ollama_default_provider_without_code_changes() -> None:
    captured: dict[str, str] = {}

    def tracking_factory(provider: str, model: str, litellm_model: str) -> BaseLLMProvider:
        _ = litellm_model
        captured["provider"] = provider
        captured["model"] = model
        return FakeProvider(provider_name=provider, model=model)

    router = LLMRouter(
        config=RouterConfig(default_provider="ollama", default_model="qwen-local"),
        provider_factory=tracking_factory,
    )

    response = await router.complete(
        system="sys",
        messages=_messages(),
        max_tokens=40,
        temperature=0.1,
    )

    assert captured == {"provider": "ollama", "model": "qwen-local"}
    assert response.provider == "ollama"


def test_router_config_reads_default_provider_and_model_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_PROVIDER", "ollama")
    monkeypatch.setenv("DEFAULT_MODEL", "llama4:scout")

    config = RouterConfig.from_env()

    assert config.default_provider == "ollama"
    assert config.default_model == "llama4:scout"


@pytest.mark.asyncio
async def test_router_complete_does_not_require_catalog_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_from_discovery(cls: type[ModelCatalog]) -> ModelCatalog:
        _ = cls
        raise AssertionError("ModelCatalog.from_discovery should not be called for complete()")

    monkeypatch.setattr(ModelCatalog, "from_discovery", classmethod(fail_from_discovery))

    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factory=_fake_factory,
    )

    response = await router.complete(
        system="sys",
        messages=_messages(),
        max_tokens=16,
        temperature=0.1,
    )

    assert response.provider == "openai"
    assert response.model == "gpt-default"


def _catalog_for_startup_validation() -> ModelCatalog:
    return ModelCatalog(
        models=(
            ModelDescriptor(
                provider="openai",
                model="gpt-default",
                litellm_model="openai/gpt-default",
                context_window=1000,
                cost_tier=1,
                suited_for=frozenset({TaskType.TRIAGE}),
            ),
        ),
    )


def test_validate_startup_passes_for_valid_defaults() -> None:
    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factory=_fake_factory,
        model_catalog=_catalog_for_startup_validation(),
    )

    router.validate_startup()


def test_validate_startup_raises_for_unregistered_default_provider() -> None:
    router = LLMRouter(
        config=RouterConfig(default_provider="ollama", default_model="gpt-default"),
        provider_factory=_fake_factory,
        model_catalog=_catalog_for_startup_validation(),
    )

    with pytest.raises(RouterStartupValidationError, match="DEFAULT_PROVIDER"):
        router.validate_startup()


def test_validate_startup_raises_for_unknown_default_model() -> None:
    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="missing-model"),
        provider_factory=_fake_factory,
        model_catalog=_catalog_for_startup_validation(),
    )

    with pytest.raises(RouterStartupValidationError, match="DEFAULT_MODEL"):
        router.validate_startup()


def test_validate_startup_surfaces_catalog_load_error() -> None:
    """When no catalog is provided and discovery fails, validate_startup raises."""
    from github_auto_maintainer.core.errors import ModelCatalogValidationError

    # Router with no catalog — _get_routing_policy will try from_discovery()
    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factory=_fake_factory,
    )

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("github_auto_maintainer.core.model_catalog.litellm") as mock_litellm,
        pytest.raises((ModelCatalogLoadError, ModelCatalogValidationError)),
    ):
        mock_litellm.model_cost = {}
        router.validate_startup()
