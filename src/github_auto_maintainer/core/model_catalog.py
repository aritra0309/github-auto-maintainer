"""Auto-discovery model catalog for deterministic routing decisions.

Replaces the static YAML-based catalog with a discovery engine that:
1. Detects available providers from environment variables.
2. Scans LiteLLM's live model registry for chat models.
3. Computes cost tiers from real pricing data (percentile bucketing, 0–5).
4. Auto-assigns suited_for task types based on tier and capabilities.
5. Discovers Ollama models via local API.
6. Applies optional overrides from config/models_override.yaml.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import httpx
import litellm
import yaml

from github_auto_maintainer.core.errors import ModelCatalogLoadError, ModelCatalogValidationError
from github_auto_maintainer.core.task_types import TaskType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ModelDescriptor — the frozen value object consumed by routing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    """Single model entry populated by auto-discovery."""

    provider: str
    model: str
    context_window: int
    cost_tier: int  # 0–5, from percentile bucketing
    suited_for: frozenset[TaskType]
    litellm_model: str
    input_cost: float = 0.0
    output_cost: float = 0.0
    supports_vision: bool = False
    supports_function_calling: bool = False


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------


class ProviderConfig(TypedDict):
    env: str
    prefix: str


PROVIDERS: dict[str, ProviderConfig] = {
    "anthropic": {"env": "ANTHROPIC_API_KEY", "prefix": "anthropic"},
    "openai": {"env": "OPENAI_API_KEY", "prefix": "openai"},
    "google": {"env": "GOOGLE_API_KEY", "prefix": "gemini"},
    "xai": {"env": "XAI_API_KEY", "prefix": "xai"},
    "ollama": {"env": "OLLAMA_API_BASE", "prefix": "ollama"},
    "openrouter": {"env": "OPENROUTER_API_KEY", "prefix": "openrouter"},
    "nvidia_nim": {"env": "NVIDIA_NIM_API_KEY", "prefix": "nvidia_nim"},
}

# Maps LiteLLM provider strings to our canonical provider names.
# LiteLLM uses its own naming (e.g. "anthropic", "openai", "vertex_ai-language-models").
# We map the ones we care about.
_LITELLM_PROVIDER_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "text-completion-openai": "openai",
    "gemini": "google",
    "vertex_ai": "google",
    "vertex_ai-language-models": "google",
    "xai": "xai",
    "openrouter": "openrouter",
    "nvidia_nim": "nvidia_nim",
}

# All task types as a frozenset for tier 4–5 models
_ALL_TASK_TYPES = frozenset(TaskType)
_MID_TASK_TYPES = frozenset(
    {TaskType.TRIAGE, TaskType.SUMMARIZATION, TaskType.CODE_REVIEW, TaskType.CLASSIFICATION}
)
_LOW_TASK_TYPES = frozenset({TaskType.TRIAGE, TaskType.SUMMARIZATION})

# Default override file location (relative to project root)
_DEFAULT_OVERRIDE_PATH = Path(__file__).resolve().parents[3] / "config" / "models_override.yaml"


# ---------------------------------------------------------------------------
# Discovery engine
# ---------------------------------------------------------------------------


class ModelDiscovery:
    """Discovers models from LiteLLM registry and Ollama API at startup."""

    def __init__(
        self,
        *,
        override_path: Path | None = None,
        env: dict[str, str] | None = None,
        model_cost: dict[str, Any] | None = None,
    ) -> None:
        self._override_path = override_path or _DEFAULT_OVERRIDE_PATH
        self._env = env if env is not None else dict(os.environ)
        self._model_cost = model_cost

    def _get_model_cost(self) -> dict[str, Any]:
        """Return the LiteLLM model cost dictionary."""
        if self._model_cost is not None:
            return self._model_cost
        try:
            result: dict[str, Any] = litellm.model_cost
            return result
        except AttributeError as exc:
            raise ModelCatalogLoadError(
                f"Failed to load litellm.model_cost: {exc}"
            ) from exc

    def detect_providers(self) -> dict[str, ProviderConfig]:
        """Return providers whose env vars are set and non-empty."""
        detected: dict[str, ProviderConfig] = {}
        for name, config in PROVIDERS.items():
            value = self._env.get(config["env"], "")
            if value.strip():
                detected[name] = config
        return detected

    def scan_models(
        self, detected_providers: dict[str, ProviderConfig]
    ) -> list[dict[str, Any]]:
        """Scan litellm.model_cost for chat models from detected providers."""
        model_cost = self._get_model_cost()
        detected_prefixes: set[str] = set()
        for config in detected_providers.values():
            detected_prefixes.add(config["prefix"])

        # Also build a set of canonical provider names for provider-based matching
        detected_canonical: set[str] = set(detected_providers.keys())

        results: list[dict[str, Any]] = []

        for key, info in model_cost.items():
            if not isinstance(info, dict):
                continue

            # Filter: chat models only
            mode = info.get("mode", "")
            if mode != "chat":
                continue

            # Determine the canonical provider from litellm_provider field
            litellm_provider = info.get("litellm_provider", "")
            canonical_provider = _LITELLM_PROVIDER_MAP.get(litellm_provider)

            if canonical_provider is None:
                # Try matching by key prefix
                for prefix_name, pconfig in detected_providers.items():
                    prefix = pconfig["prefix"]
                    if key.startswith(f"{prefix}/") or key.startswith(f"{prefix}."):
                        canonical_provider = prefix_name
                        break

            if canonical_provider is None or canonical_provider not in detected_canonical:
                continue

            # Extract model data
            input_cost = float(info.get("input_cost_per_token", 0) or 0)
            output_cost = float(info.get("output_cost_per_token", 0) or 0)
            max_input = int(info.get("max_input_tokens", 0) or 0)
            max_tokens = int(info.get("max_tokens", 0) or 0)
            context_window = max_input or max_tokens or 4096
            supports_fc = bool(info.get("supports_function_calling", False))
            supports_vision = bool(info.get("supports_vision", False))

            results.append({
                "key": key,
                "provider": canonical_provider,
                "litellm_model": key,
                "input_cost": input_cost,
                "output_cost": output_cost,
                "context_window": context_window,
                "supports_function_calling": supports_fc,
                "supports_vision": supports_vision,
            })

        return results

    def compute_tiers(
        self, models: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Assign cost_tier (0–5) based on percentile bucketing of blended cost."""
        # Separate out zero-cost models (tier 0)
        cloud_models: list[dict[str, Any]] = []
        for m in models:
            blended = (m["input_cost"] + m["output_cost"]) / 2.0 * 1_000_000
            m["_blended_cost"] = blended
            if blended <= 0:
                m["cost_tier"] = 0
            else:
                cloud_models.append(m)

        if not cloud_models:
            return models

        # Sort by blended cost for percentile computation
        costs: list[float] = sorted(m["_blended_cost"] for m in cloud_models)
        n = len(costs)

        def percentile(pct: float) -> float:
            idx = pct / 100.0 * (n - 1)
            lower = int(idx)
            upper = min(lower + 1, n - 1)
            frac = idx - lower
            return costs[lower] * (1 - frac) + costs[upper] * frac

        p25 = percentile(25)
        p50 = percentile(50)
        p75 = percentile(75)
        p90 = percentile(90)

        for m in cloud_models:
            cost = m["_blended_cost"]
            if cost <= p25:
                m["cost_tier"] = 1
            elif cost <= p50:
                m["cost_tier"] = 2
            elif cost <= p75:
                m["cost_tier"] = 3
            elif cost <= p90:
                m["cost_tier"] = 4
            else:
                m["cost_tier"] = 5

        return models

    def assign_tasks(self, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Derive suited_for from cost_tier and capabilities."""
        for m in models:
            tier = m["cost_tier"]
            supports_fc = m.get("supports_function_calling", False)

            if tier >= 4:
                m["suited_for"] = _ALL_TASK_TYPES
            elif tier >= 2:
                tasks = set(_MID_TASK_TYPES)
                if supports_fc and tier >= 3:
                    tasks.add(TaskType.PATCH_GENERATION)
                m["suited_for"] = frozenset(tasks)
            else:
                m["suited_for"] = _LOW_TASK_TYPES

        return models

    def discover_ollama(self) -> list[dict[str, Any]]:
        """Query the local Ollama API to discover installed models."""
        base_url = self._env.get("OLLAMA_API_BASE", "").strip()
        if not base_url:
            return []

        try:
            resp = httpx.get(f"{base_url}/api/tags", timeout=5.0)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except Exception:
            logger.warning(
                "ollama.discovery_failed",
                extra={"base_url": base_url},
            )
            return []

        raw_models = data.get("models", [])
        if not isinstance(raw_models, list):
            return []

        results: list[dict[str, Any]] = []
        for entry in raw_models:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "")
            if not name:
                continue

            results.append({
                "key": f"ollama/{name}",
                "provider": "ollama",
                "litellm_model": f"ollama/{name}",
                "input_cost": 0.0,
                "output_cost": 0.0,
                "context_window": 128000,
                "supports_function_calling": False,
                "supports_vision": False,
                "cost_tier": 0,
                "suited_for": _LOW_TASK_TYPES,
            })

        return results

    def apply_overrides(
        self, models: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Apply optional config/models_override.yaml adjustments."""
        if not self._override_path.exists():
            return models

        try:
            raw_text = self._override_path.read_text(encoding="utf-8")
            loaded = yaml.safe_load(raw_text)
        except Exception as exc:
            logger.warning(
                "override.load_failed",
                extra={"path": str(self._override_path), "error": str(exc)},
            )
            return models

        if not isinstance(loaded, dict):
            return models

        overrides = loaded.get("overrides", [])
        if not isinstance(overrides, list):
            return models

        # Index models by litellm_model key for fast lookup
        model_index: dict[str, dict[str, Any]] = {
            m["litellm_model"]: m for m in models
        }

        for entry in overrides:
            if not isinstance(entry, dict):
                continue
            model_key = entry.get("model", "")
            if not model_key:
                continue

            target = model_index.get(model_key)
            if target is None:
                continue

            # Exclude
            if entry.get("exclude", False):
                models = [m for m in models if m["litellm_model"] != model_key]
                continue

            # Override cost_tier
            if "cost_tier" in entry:
                tier_val = entry["cost_tier"]
                if isinstance(tier_val, int) and 0 <= tier_val <= 5:
                    target["cost_tier"] = tier_val

            # Override suited_for
            if "suited_for" in entry:
                raw_tasks = entry["suited_for"]
                if isinstance(raw_tasks, list):
                    parsed: set[TaskType] = set()
                    for t in raw_tasks:
                        try:
                            parsed.add(TaskType(str(t).strip().lower()))
                        except ValueError:
                            pass
                    if parsed:
                        target["suited_for"] = frozenset(parsed)

        return models

    def build_catalog(self) -> ModelCatalog:
        """Run full discovery pipeline and return a frozen ModelCatalog."""
        detected = self.detect_providers()
        if not detected:
            raise ModelCatalogValidationError(
                "No LLM providers detected. Set at least one API key environment variable "
                "(e.g. ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY). "
                f"Checked: {', '.join(c['env'] for c in PROVIDERS.values())}"
            )

        # Scan LiteLLM registry (excludes ollama — handled separately)
        models = self.scan_models(detected)

        # Discover Ollama models via local API
        if "ollama" in detected:
            ollama_models = self.discover_ollama()
            models.extend(ollama_models)

        # Compute tiers and assign tasks
        models = self.compute_tiers(models)
        models = self.assign_tasks(models)

        # Apply optional overrides
        models = self.apply_overrides(models)

        if not models:
            provider_names = ", ".join(detected.keys())
            raise ModelCatalogValidationError(
                f"Providers detected ({provider_names}) but zero chat models found. "
                "Check that your API keys are valid and litellm is up to date."
            )

        # Build frozen descriptors
        descriptors: list[ModelDescriptor] = []
        seen: set[str] = set()
        for m in models:
            litellm_model = m["litellm_model"]
            if litellm_model in seen:
                continue
            seen.add(litellm_model)

            # Extract model name from key (strip provider prefix)
            model_name = litellm_model
            for pconfig in PROVIDERS.values():
                prefix = pconfig["prefix"] + "/"
                if litellm_model.startswith(prefix):
                    model_name = litellm_model[len(prefix):]
                    break

            descriptors.append(
                ModelDescriptor(
                    provider=m["provider"],
                    model=model_name,
                    context_window=m["context_window"],
                    cost_tier=m["cost_tier"],
                    suited_for=m.get("suited_for", _LOW_TASK_TYPES),
                    litellm_model=litellm_model,
                    input_cost=m.get("input_cost", 0.0),
                    output_cost=m.get("output_cost", 0.0),
                    supports_vision=m.get("supports_vision", False),
                    supports_function_calling=m.get("supports_function_calling", False),
                )
            )

        # Sort for deterministic ordering: provider, then model name
        descriptors.sort(key=lambda d: (d.provider, d.model))

        logger.info(
            "model_catalog.discovered",
            extra={
                "providers": list(detected.keys()),
                "model_count": len(descriptors),
            },
        )

        return ModelCatalog(models=tuple(descriptors))


# ---------------------------------------------------------------------------
# ModelCatalog — public API consumed by routing policy and router
# ---------------------------------------------------------------------------


class ModelCatalog:
    """In-memory validated model catalog."""

    def __init__(self, models: tuple[ModelDescriptor, ...]) -> None:
        if not models:
            raise ModelCatalogValidationError(
                "Model catalog must contain at least one model entry"
            )
        self._models = models

    @property
    def models(self) -> tuple[ModelDescriptor, ...]:
        """Return all validated models in deterministic order."""
        return self._models

    @classmethod
    def from_discovery(
        cls,
        *,
        override_path: Path | None = None,
        env: dict[str, str] | None = None,
        model_cost: dict[str, Any] | None = None,
    ) -> ModelCatalog:
        """Build a catalog via auto-discovery from LiteLLM registry + Ollama API."""
        discovery = ModelDiscovery(
            override_path=override_path,
            env=env,
            model_cost=model_cost,
        )
        return discovery.build_catalog()

    def get_models_for_task(self, task_type: TaskType) -> tuple[ModelDescriptor, ...]:
        """Return models suited for a given task type."""
        return tuple(d for d in self._models if task_type in d.suited_for)

    def get_all_models(self) -> tuple[ModelDescriptor, ...]:
        """Return all models in the catalog."""
        return self._models
