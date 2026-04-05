"""Typed model catalog loader for deterministic routing decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from github_auto_maintainer.core.errors import ModelCatalogLoadError, ModelCatalogValidationError
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    """Single model entry loaded from the catalog."""

    provider: str
    model: str
    context_window: int
    cost_tier: TaskComplexity
    suited_for: frozenset[TaskType]


class ModelCatalog:
    """In-memory validated model catalog."""

    def __init__(self, models: tuple[ModelDescriptor, ...], source_path: Path) -> None:
        if not models:
            raise ModelCatalogValidationError("Model catalog must contain at least one model entry")
        self._models = models
        self._source_path = source_path

    @property
    def models(self) -> tuple[ModelDescriptor, ...]:
        """Return all validated models in deterministic order."""

        return self._models

    @property
    def source_path(self) -> Path:
        """Return the absolute source path used to load this catalog."""

        return self._source_path

    @classmethod
    def from_yaml(cls, path: Path | str) -> ModelCatalog:
        """Load and validate a model catalog from a YAML file."""

        resolved = Path(path).expanduser().resolve()
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise ModelCatalogLoadError(f"Failed to load model catalog at '{resolved}': {exc}") from exc

        try:
            loaded = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise ModelCatalogLoadError(
                f"Failed to parse model catalog YAML at '{resolved}': {exc}"
            ) from exc

        models = _parse_models(loaded, source_path=resolved)
        return cls(models=models, source_path=resolved)


def _parse_models(payload: object, source_path: Path) -> tuple[ModelDescriptor, ...]:
    if not isinstance(payload, dict):
        raise ModelCatalogValidationError(
            f"Catalog root must be a mapping with a 'models' key (file: '{source_path}')"
        )

    model_rows = payload.get("models")
    if not isinstance(model_rows, list):
        raise ModelCatalogValidationError(
            f"'models' must be a list in catalog file '{source_path}'"
        )

    descriptors: list[ModelDescriptor] = []
    seen: set[tuple[str, str]] = set()

    for index, row in enumerate(model_rows, start=1):
        if not isinstance(row, dict):
            raise ModelCatalogValidationError(
                f"Model entry #{index} must be a mapping in catalog file '{source_path}'"
            )

        provider = _require_string(row, key="provider", source_path=source_path, index=index).lower()
        model = _require_string(row, key="model", source_path=source_path, index=index)
        context_window = _require_positive_int(
            row,
            key="context_window",
            source_path=source_path,
            index=index,
        )
        cost_tier = _require_cost_tier(row, source_path=source_path, index=index)
        suited_for = _require_task_types(row, source_path=source_path, index=index)

        key = (provider, model)
        if key in seen:
            raise ModelCatalogValidationError(
                f"Duplicate model entry '{provider}/{model}' at row #{index} in '{source_path}'"
            )
        seen.add(key)

        descriptors.append(
            ModelDescriptor(
                provider=provider,
                model=model,
                context_window=context_window,
                cost_tier=cost_tier,
                suited_for=suited_for,
            )
        )

    return tuple(descriptors)


def _require_string(
    row: dict[str, Any],
    *,
    key: str,
    source_path: Path,
    index: int,
) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelCatalogValidationError(
            f"Model entry #{index} must contain non-empty string '{key}' in '{source_path}'"
        )
    return value.strip()


def _require_positive_int(
    row: dict[str, Any],
    *,
    key: str,
    source_path: Path,
    index: int,
) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ModelCatalogValidationError(
            f"Model entry #{index} must contain positive integer '{key}' in '{source_path}'"
        )
    return value


def _require_cost_tier(
    row: dict[str, Any],
    *,
    source_path: Path,
    index: int,
) -> TaskComplexity:
    raw = row.get("cost_tier")
    if not isinstance(raw, str):
        raise ModelCatalogValidationError(
            f"Model entry #{index} must contain string 'cost_tier' in '{source_path}'"
        )

    try:
        return TaskComplexity(raw.strip().lower())
    except ValueError as exc:
        raise ModelCatalogValidationError(
            f"Model entry #{index} has invalid cost_tier '{raw}' in '{source_path}'"
        ) from exc


def _require_task_types(
    row: dict[str, Any],
    *,
    source_path: Path,
    index: int,
) -> frozenset[TaskType]:
    raw = row.get("suited_for")
    if not isinstance(raw, list) or not raw:
        raise ModelCatalogValidationError(
            f"Model entry #{index} must contain non-empty list 'suited_for' in '{source_path}'"
        )

    parsed: set[TaskType] = set()
    for task_name in raw:
        if not isinstance(task_name, str) or not task_name.strip():
            raise ModelCatalogValidationError(
                f"Model entry #{index} has invalid suited_for value '{task_name}' in '{source_path}'"
            )
        try:
            parsed.add(TaskType(task_name.strip().lower()))
        except ValueError as exc:
            raise ModelCatalogValidationError(
                f"Model entry #{index} has unknown task type '{task_name}' in '{source_path}'"
            ) from exc

    return frozenset(parsed)
