"""Tests for ModelCatalog with auto-discovery (replaces YAML-based tests)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from github_auto_maintainer.core.errors import ModelCatalogValidationError
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.task_types import TaskType


def _descriptor(
    *,
    provider: str = "openai",
    model: str = "gpt-5.4-mini",
    cost_tier: int = 1,
    suited_for: frozenset[TaskType] | None = None,
    context_window: int = 1_000_000,
) -> ModelDescriptor:
    return ModelDescriptor(
        provider=provider,
        model=model,
        litellm_model=f"{provider}/{model}",
        context_window=context_window,
        cost_tier=cost_tier,
        suited_for=suited_for or frozenset({TaskType.TRIAGE}),
    )


def test_model_catalog_stores_models() -> None:
    m = _descriptor()
    catalog = ModelCatalog(models=(m,))

    assert len(catalog.models) == 1
    assert catalog.models[0] is m


def test_model_catalog_rejects_empty_models() -> None:
    with pytest.raises(ModelCatalogValidationError, match="at least one model"):
        ModelCatalog(models=())


def test_get_models_for_task_filters_correctly() -> None:
    triage_model = _descriptor(model="triage-model", suited_for=frozenset({TaskType.TRIAGE}))
    summary_model = _descriptor(
        model="summary-model",
        suited_for=frozenset({TaskType.SUMMARIZATION}),
    )
    catalog = ModelCatalog(models=(triage_model, summary_model))

    triage_results = catalog.get_models_for_task(TaskType.TRIAGE)
    assert len(triage_results) == 1
    assert triage_results[0].model == "triage-model"

    summary_results = catalog.get_models_for_task(TaskType.SUMMARIZATION)
    assert len(summary_results) == 1
    assert summary_results[0].model == "summary-model"


def test_get_all_models_returns_all() -> None:
    m1 = _descriptor(model="a")
    m2 = _descriptor(model="b")
    catalog = ModelCatalog(models=(m1, m2))

    assert len(catalog.get_all_models()) == 2


def test_model_descriptor_cost_tier_is_int() -> None:
    m = _descriptor(cost_tier=3)
    assert isinstance(m.cost_tier, int)
    assert m.cost_tier == 3


def test_model_descriptor_new_fields_default() -> None:
    m = _descriptor()
    assert m.input_cost == 0.0
    assert m.output_cost == 0.0
    assert m.supports_vision is False
    assert m.supports_function_calling is False


def test_model_descriptor_new_fields_set() -> None:
    m = ModelDescriptor(
        provider="openai",
        model="gpt-5.4",
        litellm_model="openai/gpt-5.4",
        context_window=1_000_000,
        cost_tier=5,
        suited_for=frozenset({TaskType.TRIAGE}),
        input_cost=15.0,
        output_cost=60.0,
        supports_vision=True,
        supports_function_calling=True,
    )
    assert m.input_cost == 15.0
    assert m.output_cost == 60.0
    assert m.supports_vision is True
    assert m.supports_function_calling is True


def test_from_discovery_with_mocked_litellm() -> None:
    """from_discovery() should detect providers, scan litellm.model_cost, and build catalog."""
    fake_model_cost = {
        "openai/gpt-5.4-mini": {
            "litellm_provider": "openai",
            "mode": "chat",
            "max_tokens": 128000,
            "max_input_tokens": 128000,
            "max_output_tokens": 16384,
            "input_cost_per_token": 0.0000005,
            "output_cost_per_token": 0.0000015,
            "supports_function_calling": True,
            "supports_vision": False,
        },
    }
    env = {"OPENAI_API_KEY": "sk-test"}

    with (
        patch.dict("os.environ", env, clear=True),
        patch("github_auto_maintainer.core.model_catalog.litellm") as mock_litellm,
    ):
        mock_litellm.model_cost = fake_model_cost
        catalog = ModelCatalog.from_discovery()

    assert len(catalog.models) >= 1
    names = [m.model for m in catalog.models]
    assert "gpt-5.4-mini" in names or "openai/gpt-5.4-mini" in names


def test_from_discovery_no_api_keys_raises() -> None:
    """If no provider API keys are set, from_discovery() should fail fast."""
    env: dict[str, str] = {}

    with (
        patch.dict("os.environ", env, clear=True),
        patch("github_auto_maintainer.core.model_catalog.litellm") as mock_litellm,
        pytest.raises((RuntimeError, ModelCatalogValidationError)),
    ):
        mock_litellm.model_cost = {}
        ModelCatalog.from_discovery()
