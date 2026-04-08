"""Regression test: TRIAGE at MEDIUM complexity must select a medium-tier model."""

from __future__ import annotations

from github_auto_maintainer.core.model_catalog import ModelCatalog
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.settings import default_model_catalog_path
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType


def test_triage_medium_selects_sonnet() -> None:
    """TRIAGE + MEDIUM must route to anthropic/claude-sonnet-4-6."""
    catalog = ModelCatalog.from_yaml(default_model_catalog_path())
    policy = RoutingPolicy(catalog)

    selected = policy.select(task_type=TaskType.TRIAGE, complexity=TaskComplexity.MEDIUM)

    assert selected.provider == "anthropic"
    assert selected.model == "claude-sonnet-4-6"
    assert selected.cost_tier == TaskComplexity.MEDIUM
