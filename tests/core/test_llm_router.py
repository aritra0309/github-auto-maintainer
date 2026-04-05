from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from tenacity import wait_none

from github_auto_maintainer.core.errors import (
    ModelCatalogLoadError,
    RouterStartupValidationError,
    TransientProviderError,
    UnknownProviderError,
)
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.hooks import HookBus
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig, RouterRetryConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
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


@pytest.mark.asyncio
async def test_router_uses_defaults_when_override_not_passed() -> None:
    used: dict[str, str] = {}

    def fake_factory(model: str) -> BaseLLMProvider:
        used["model"] = model
        return FakeProvider(provider_name="openai", model=model)

    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factories={"openai": fake_factory},
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

    def openai_factory(model: str) -> BaseLLMProvider:
        used["provider"] = "openai"
        used["model"] = model
        return FakeProvider(provider_name="openai", model=model)

    def ollama_factory(model: str) -> BaseLLMProvider:
        used["provider"] = "ollama"
        used["model"] = model
        return FakeProvider(provider_name="ollama", model=model)

    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factories={"openai": openai_factory, "ollama": ollama_factory},
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
async def test_router_raises_unknown_provider_for_unregistered_key() -> None:
    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factories={},
    )

    with pytest.raises(UnknownProviderError):
        await router.complete(
            system="sys",
            messages=_messages(),
            max_tokens=16,
            temperature=0.1,
            provider="does-not-exist",
        )


@pytest.mark.asyncio
async def test_router_retries_transient_failures_then_succeeds() -> None:
    flaky = FlakyProvider(provider_name="openai", model="gpt-retry")

    def flaky_factory(model: str) -> BaseLLMProvider:
        _ = model
        return flaky

    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-retry"),
        retry_config=RouterRetryConfig(max_attempts=4, wait_strategy=wait_none()),
        provider_factories={"openai": flaky_factory},
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

    def fake_factory(model: str) -> BaseLLMProvider:
        return FakeProvider(provider_name="openai", model=model)

    router = LLMRouter(
        hook_bus=bus,
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factories={"openai": fake_factory},
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

    def ollama_factory(model: str) -> BaseLLMProvider:
        captured["provider"] = "ollama"
        captured["model"] = model
        return FakeProvider(provider_name="ollama", model=model)

    router = LLMRouter(
        config=RouterConfig(default_provider="ollama", default_model="qwen-local"),
        provider_factories={"ollama": ollama_factory},
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
    def fake_factory(model: str) -> BaseLLMProvider:
        return FakeProvider(provider_name="openai", model=model)

    def fail_from_yaml(cls: type[ModelCatalog], path: object) -> ModelCatalog:
        _ = (cls, path)
        raise AssertionError("ModelCatalog.from_yaml should not be called for complete()")

    monkeypatch.setattr(ModelCatalog, "from_yaml", classmethod(fail_from_yaml))

    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factories={"openai": fake_factory},
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
                context_window=1000,
                cost_tier=TaskComplexity.LOW,
                suited_for=frozenset({TaskType.TRIAGE}),
            ),
        ),
        source_path=Path("/tmp/test-catalog.yaml"),
    )


def test_validate_startup_passes_for_valid_defaults() -> None:
    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factories={"openai": lambda model: FakeProvider(provider_name="openai", model=model)},
        model_catalog=_catalog_for_startup_validation(),
    )

    router.validate_startup()


def test_validate_startup_raises_for_unregistered_default_provider() -> None:
    router = LLMRouter(
        config=RouterConfig(default_provider="ollama", default_model="gpt-default"),
        provider_factories={"openai": lambda model: FakeProvider(provider_name="openai", model=model)},
        model_catalog=_catalog_for_startup_validation(),
    )

    with pytest.raises(RouterStartupValidationError, match="DEFAULT_PROVIDER"):
        router.validate_startup()


def test_validate_startup_raises_for_unknown_default_model() -> None:
    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="missing-model"),
        provider_factories={"openai": lambda model: FakeProvider(provider_name="openai", model=model)},
        model_catalog=_catalog_for_startup_validation(),
    )

    with pytest.raises(RouterStartupValidationError, match="DEFAULT_MODEL"):
        router.validate_startup()


def test_validate_startup_surfaces_catalog_load_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_CATALOG_PATH", "/tmp/this-file-does-not-exist.yaml")
    router = LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-default"),
        provider_factories={"openai": lambda model: FakeProvider(provider_name="openai", model=model)},
    )

    with pytest.raises(ModelCatalogLoadError, match="/tmp/this-file-does-not-exist.yaml"):
        router.validate_startup()
