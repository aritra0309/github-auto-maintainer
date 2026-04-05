from __future__ import annotations

from pathlib import Path

import pytest

from github_auto_maintainer.core.errors import ModelCatalogValidationError
from github_auto_maintainer.core.model_catalog import ModelCatalog
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType


def test_model_catalog_loads_valid_yaml(tmp_path: Path) -> None:
    catalog_file = tmp_path / "models.yaml"
    catalog_file.write_text(
        """
models:
  - provider: openai
    model: gpt-5.4-mini
    context_window: 1000000
    cost_tier: low
    suited_for:
      - triage
      - summarization
""".strip(),
        encoding="utf-8",
    )

    catalog = ModelCatalog.from_yaml(catalog_file)

    assert catalog.source_path == catalog_file.resolve()
    assert len(catalog.models) == 1
    descriptor = catalog.models[0]
    assert descriptor.provider == "openai"
    assert descriptor.model == "gpt-5.4-mini"
    assert descriptor.cost_tier is TaskComplexity.LOW
    assert descriptor.suited_for == frozenset({TaskType.TRIAGE, TaskType.SUMMARIZATION})


def test_model_catalog_raises_for_missing_required_key(tmp_path: Path) -> None:
    catalog_file = tmp_path / "missing_provider.yaml"
    catalog_file.write_text(
        """
models:
  - model: gpt-5.4-mini
    context_window: 1000000
    cost_tier: low
    suited_for:
      - triage
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ModelCatalogValidationError, match="provider"):
        ModelCatalog.from_yaml(catalog_file)


def test_model_catalog_raises_for_invalid_cost_tier(tmp_path: Path) -> None:
    catalog_file = tmp_path / "invalid_tier.yaml"
    catalog_file.write_text(
        """
models:
  - provider: openai
    model: gpt-5.4-mini
    context_window: 1000000
    cost_tier: ultra
    suited_for:
      - triage
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ModelCatalogValidationError, match="invalid cost_tier"):
        ModelCatalog.from_yaml(catalog_file)


def test_model_catalog_raises_for_invalid_task_type(tmp_path: Path) -> None:
    catalog_file = tmp_path / "invalid_task_type.yaml"
    catalog_file.write_text(
        """
models:
  - provider: openai
    model: gpt-5.4-mini
    context_window: 1000000
    cost_tier: low
    suited_for:
      - not_a_real_task
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ModelCatalogValidationError, match="unknown task type"):
        ModelCatalog.from_yaml(catalog_file)
