"""Dedicated tests for the ModelDiscovery engine.

Tests cover: provider detection, model filtering, tier percentile math,
Ollama API mocking, override file parsing, edge cases.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from github_auto_maintainer.core.model_catalog import ModelDiscovery
from github_auto_maintainer.core.task_types import TaskType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_model_cost_entry(
    *,
    provider: str = "openai",
    mode: str = "chat",
    max_tokens: int = 128000,
    input_cost: float = 0.000001,
    output_cost: float = 0.000002,
    supports_fc: bool = True,
    supports_vision: bool = False,
) -> dict[str, Any]:
    return {
        "litellm_provider": provider,
        "mode": mode,
        "max_tokens": max_tokens,
        "max_input_tokens": max_tokens,
        "max_output_tokens": max_tokens // 4,
        "input_cost_per_token": input_cost,
        "output_cost_per_token": output_cost,
        "supports_function_calling": supports_fc,
        "supports_vision": supports_vision,
    }


def _build_fake_model_cost(
    count: int = 5,
    provider: str = "openai",
    base_input_cost: float = 0.000001,
    cost_spread: float = 10.0,
) -> dict[str, Any]:
    """Build a fake model_cost dict with spread pricing for tier tests."""
    result: dict[str, Any] = {}
    for i in range(count):
        multiplier = cost_spread ** (i / max(count - 1, 1))
        key = f"{provider}/model-{i}"
        result[key] = _make_model_cost_entry(
            provider=provider,
            input_cost=base_input_cost * multiplier,
            output_cost=base_input_cost * multiplier * 2,
        )
    return result


def _make_scanned_model(
    key: str,
    *,
    input_cost: float = 0.000001,
    output_cost: float = 0.000002,
    context_window: int = 128000,
    supports_fc: bool = True,
    supports_vision: bool = False,
    provider: str = "openai",
) -> dict[str, Any]:
    """Build a model dict as returned by scan_models()."""
    return {
        "key": key,
        "litellm_model": key,
        "provider": provider,
        "context_window": context_window,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "supports_function_calling": supports_fc,
        "supports_vision": supports_vision,
    }


# ---------------------------------------------------------------------------
# Provider Detection
# ---------------------------------------------------------------------------

class TestProviderDetection:

    def test_detects_single_provider(self) -> None:
        env = {"OPENAI_API_KEY": "sk-test"}
        discovery = ModelDiscovery(env=env)
        detected = discovery.detect_providers()
        assert "openai" in detected

    def test_detects_multiple_providers(self) -> None:
        env = {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-ant-test"}
        discovery = ModelDiscovery(env=env)
        detected = discovery.detect_providers()
        assert "openai" in detected
        assert "anthropic" in detected

    def test_no_keys_returns_empty(self) -> None:
        discovery = ModelDiscovery(env={})
        detected = discovery.detect_providers()
        assert len(detected) == 0

    def test_detects_all_seven_providers(self) -> None:
        env = {
            "ANTHROPIC_API_KEY": "sk-ant",
            "OPENAI_API_KEY": "sk-oai",
            "GOOGLE_API_KEY": "goog",
            "XAI_API_KEY": "xai",
            "OLLAMA_API_BASE": "http://localhost:11434",
            "OPENROUTER_API_KEY": "or-key",
            "NVIDIA_NIM_API_KEY": "nim-key",
        }
        discovery = ModelDiscovery(env=env)
        detected = discovery.detect_providers()
        assert len(detected) == 7

    def test_ignores_empty_api_key(self) -> None:
        env = {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "   "}
        discovery = ModelDiscovery(env=env)
        detected = discovery.detect_providers()
        assert len(detected) == 0


# ---------------------------------------------------------------------------
# Model Scanning
# ---------------------------------------------------------------------------

class TestModelScanning:

    def test_scans_chat_models_only(self) -> None:
        model_cost = {
            "openai/gpt-5.4": _make_model_cost_entry(provider="openai", mode="chat"),
            "openai/text-embedding-3-large": _make_model_cost_entry(
                provider="openai", mode="embedding",
            ),
            "openai/dall-e-3": _make_model_cost_entry(provider="openai", mode="image_generation"),
        }
        env = {"OPENAI_API_KEY": "sk-test"}
        discovery = ModelDiscovery(env=env, model_cost=model_cost)
        detected = discovery.detect_providers()
        models = discovery.scan_models(detected)

        model_keys = [m["litellm_model"] for m in models]
        assert "openai/gpt-5.4" in model_keys
        assert "openai/text-embedding-3-large" not in model_keys
        assert "openai/dall-e-3" not in model_keys

    def test_filters_by_detected_provider(self) -> None:
        model_cost = {
            "openai/gpt-5.4": _make_model_cost_entry(provider="openai"),
            "anthropic/claude-sonnet-4-6": _make_model_cost_entry(provider="anthropic"),
        }
        env = {"OPENAI_API_KEY": "sk-test"}  # Only OpenAI key set
        discovery = ModelDiscovery(env=env, model_cost=model_cost)
        detected = discovery.detect_providers()
        models = discovery.scan_models(detected)

        model_keys = [m["litellm_model"] for m in models]
        assert "openai/gpt-5.4" in model_keys
        assert "anthropic/claude-sonnet-4-6" not in model_keys

    def test_empty_model_cost_returns_empty(self) -> None:
        env = {"OPENAI_API_KEY": "sk-test"}
        discovery = ModelDiscovery(env=env, model_cost={})
        detected = discovery.detect_providers()
        models = discovery.scan_models(detected)
        assert len(models) == 0


# ---------------------------------------------------------------------------
# Tier Bucketing
# ---------------------------------------------------------------------------

class TestTierBucketing:

    def test_single_model_gets_tier_1(self) -> None:
        """A single cloud model should get a reasonable tier, not 0."""
        discovery = ModelDiscovery(env={}, model_cost={})
        models = [_make_scanned_model("openai/gpt-5.4", input_cost=0.000001, output_cost=0.000002)]
        tiered = discovery.compute_tiers(models)
        # Single model — should be tier 1 (cheapest bucket for cloud)
        assert tiered[0]["cost_tier"] >= 1

    def test_all_same_price_get_same_tier(self) -> None:
        discovery = ModelDiscovery(env={}, model_cost={})
        models = [
            _make_scanned_model(f"openai/model-{i}", input_cost=0.000001, output_cost=0.000002)
            for i in range(10)
        ]
        tiered = discovery.compute_tiers(models)
        tier_values = {m["cost_tier"] for m in tiered}
        # All same price → should all be in the same tier
        assert len(tier_values) == 1

    def test_local_models_get_tier_0(self) -> None:
        discovery = ModelDiscovery(env={}, model_cost={})
        models = [_make_scanned_model("ollama/llama4:scout", input_cost=0.0, output_cost=0.0, provider="ollama")]
        tiered = discovery.compute_tiers(models)
        assert tiered[0]["cost_tier"] == 0

    def test_spread_prices_produce_multiple_tiers(self) -> None:
        """Models with widely spread prices should land in different tiers."""
        discovery = ModelDiscovery(env={}, model_cost={})
        models = [
            _make_scanned_model("openai/cheap", input_cost=0.00000001, output_cost=0.00000002),
            _make_scanned_model("openai/mid-low", input_cost=0.0000001, output_cost=0.0000002),
            _make_scanned_model("openai/mid", input_cost=0.000001, output_cost=0.000002),
            _make_scanned_model("openai/mid-high", input_cost=0.00001, output_cost=0.00002),
            _make_scanned_model("openai/expensive", input_cost=0.0001, output_cost=0.0002),
            _make_scanned_model("openai/premium", input_cost=0.001, output_cost=0.002),
        ]
        tiered = discovery.compute_tiers(models)
        tier_by_key = {m["litellm_model"]: m["cost_tier"] for m in tiered}
        # Cheapest should be lower tier than most expensive
        assert tier_by_key["openai/cheap"] < tier_by_key["openai/premium"]

    def test_tiers_are_in_range_0_to_5(self) -> None:
        discovery = ModelDiscovery(env={}, model_cost={})
        models = [
            _make_scanned_model(
                f"openai/model-{i}",
                input_cost=float(i + 1) * 0.000001,
                output_cost=float(i + 1) * 0.000002,
            )
            for i in range(100)
        ]
        tiered = discovery.compute_tiers(models)
        for m in tiered:
            assert 0 <= m["cost_tier"] <= 5


# ---------------------------------------------------------------------------
# Task Assignment
# ---------------------------------------------------------------------------

class TestTaskAssignment:

    def test_high_tier_gets_all_tasks(self) -> None:
        discovery = ModelDiscovery(env={}, model_cost={})
        models = [_make_scanned_model("openai/gpt-5", input_cost=0.001, output_cost=0.002)]
        models[0]["cost_tier"] = 5
        result = discovery.assign_tasks(models)
        # Should include all task types
        assert TaskType.TRIAGE in result[0]["suited_for"]
        assert TaskType.PATCH_GENERATION in result[0]["suited_for"]

    def test_low_tier_gets_limited_tasks(self) -> None:
        discovery = ModelDiscovery(env={}, model_cost={})
        models = [_make_scanned_model("openai/cheap", input_cost=0.0000001, output_cost=0.0000002)]
        models[0]["cost_tier"] = 1
        models[0]["supports_function_calling"] = False
        result = discovery.assign_tasks(models)
        assert TaskType.TRIAGE in result[0]["suited_for"]
        assert TaskType.SUMMARIZATION in result[0]["suited_for"]
        # Should NOT include expensive tasks
        assert TaskType.PATCH_GENERATION not in result[0]["suited_for"]

    def test_mid_tier_with_function_calling(self) -> None:
        discovery = ModelDiscovery(env={}, model_cost={})
        models = [_make_scanned_model("openai/mid", input_cost=0.00001, output_cost=0.00002)]
        models[0]["cost_tier"] = 3
        models[0]["supports_function_calling"] = True
        result = discovery.assign_tasks(models)
        assert TaskType.TRIAGE in result[0]["suited_for"]
        assert TaskType.PATCH_GENERATION in result[0]["suited_for"]


# ---------------------------------------------------------------------------
# Ollama Discovery
# ---------------------------------------------------------------------------

class TestOllamaDiscovery:

    def test_ollama_models_discovered(self) -> None:
        """When Ollama API returns models, they should be created at tier 0."""
        ollama_response = {
            "models": [
                {"name": "llama4:scout", "size": 7_000_000_000},
                {"name": "codellama:13b", "size": 13_000_000_000},
            ]
        }
        env = {"OLLAMA_API_BASE": "http://localhost:11434"}
        discovery = ModelDiscovery(env=env, model_cost={})

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = ollama_response

        with patch("httpx.get", return_value=mock_response):
            models = discovery.discover_ollama()

        assert len(models) == 2
        for m in models:
            assert m["cost_tier"] == 0
            assert m["provider"] == "ollama"

    def test_ollama_unreachable_returns_empty(self) -> None:
        """If Ollama API is unreachable, return empty list (don't crash)."""
        env = {"OLLAMA_API_BASE": "http://localhost:11434"}
        discovery = ModelDiscovery(env=env, model_cost={})

        with patch("httpx.get", side_effect=Exception("Connection refused")):
            models = discovery.discover_ollama()

        assert len(models) == 0

    def test_ollama_empty_response(self) -> None:
        """If Ollama returns no models, return empty list."""
        env = {"OLLAMA_API_BASE": "http://localhost:11434"}
        discovery = ModelDiscovery(env=env, model_cost={})

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        with patch("httpx.get", return_value=mock_response):
            models = discovery.discover_ollama()

        assert len(models) == 0


# ---------------------------------------------------------------------------
# Override File
# ---------------------------------------------------------------------------

class TestOverrideApplication:

    def test_exclude_model(self, tmp_path: Any) -> None:
        """Override with exclude: true should remove a model."""
        override_file = tmp_path / "models_override.yaml"
        override_file.write_text(
            yaml.dump({"overrides": [{"model": "openai/gpt-5.4", "exclude": True}]}),
            encoding="utf-8",
        )

        models = [
            _make_scanned_model("openai/gpt-5.4"),
            _make_scanned_model("openai/gpt-5.4-mini"),
        ]

        discovery = ModelDiscovery(env={}, model_cost={}, override_path=override_file)
        result = discovery.apply_overrides(models)

        names = [m["litellm_model"] for m in result]
        assert "openai/gpt-5.4" not in names
        assert "openai/gpt-5.4-mini" in names

    def test_override_cost_tier(self, tmp_path: Any) -> None:
        """Override cost_tier for a specific model."""
        override_file = tmp_path / "models_override.yaml"
        override_file.write_text(
            yaml.dump({"overrides": [{"model": "openai/gpt-5.4-mini", "cost_tier": 4}]}),
            encoding="utf-8",
        )

        models = [_make_scanned_model("openai/gpt-5.4-mini")]
        models[0]["cost_tier"] = 1

        discovery = ModelDiscovery(env={}, model_cost={}, override_path=override_file)
        result = discovery.apply_overrides(models)

        assert result[0]["cost_tier"] == 4

    def test_override_suited_for(self, tmp_path: Any) -> None:
        """Override suited_for for a specific model."""
        override_file = tmp_path / "models_override.yaml"
        override_file.write_text(
            yaml.dump({"overrides": [
                {"model": "openai/gpt-5.4-mini", "suited_for": ["patch_generation", "deep_review"]},
            ]}),
            encoding="utf-8",
        )

        models = [_make_scanned_model("openai/gpt-5.4-mini")]
        models[0]["suited_for"] = frozenset({TaskType.TRIAGE})

        discovery = ModelDiscovery(env={}, model_cost={}, override_path=override_file)
        result = discovery.apply_overrides(models)

        assert TaskType.PATCH_GENERATION in result[0]["suited_for"]

    def test_no_override_file_is_noop(self, tmp_path: Any) -> None:
        """If override file doesn't exist, models pass through unchanged."""
        nonexistent = tmp_path / "does_not_exist.yaml"

        models = [_make_scanned_model("openai/gpt-5.4-mini")]
        models[0]["cost_tier"] = 1

        discovery = ModelDiscovery(env={}, model_cost={}, override_path=nonexistent)
        result = discovery.apply_overrides(models)

        assert len(result) == 1
        assert result[0]["cost_tier"] == 1


# ---------------------------------------------------------------------------
# Full Build Catalog (Integration)
# ---------------------------------------------------------------------------

class TestBuildCatalog:

    def test_build_with_single_provider(self) -> None:
        """End-to-end: detect one provider, scan models, build catalog."""
        fake_model_cost = _build_fake_model_cost(count=3, provider="openai")
        env = {"OPENAI_API_KEY": "sk-test"}

        with patch.dict("os.environ", env, clear=True):
            discovery = ModelDiscovery(model_cost=fake_model_cost)
            catalog = discovery.build_catalog()

        assert len(catalog.models) >= 1
        providers = {m.provider for m in catalog.models}
        assert "openai" in providers

    def test_build_with_zero_providers_raises(self) -> None:
        """No API keys → should fail fast."""
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(Exception),
        ):
            discovery = ModelDiscovery(model_cost={})
            discovery.build_catalog()

    def test_provider_detected_but_zero_chat_models(self) -> None:
        """Provider key set but no chat models → should raise."""
        fake_model_cost = {
            "openai/text-embedding-3-large": _make_model_cost_entry(
                provider="openai", mode="embedding",
            ),
        }
        env = {"OPENAI_API_KEY": "sk-test"}

        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(Exception),
        ):
            discovery = ModelDiscovery(model_cost=fake_model_cost)
            discovery.build_catalog()
