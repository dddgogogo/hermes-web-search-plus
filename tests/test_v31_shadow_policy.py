from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import shadow_policy_v3 as shadow
from orchestrator_v3 import ProviderPlan


def _request(query: str = "quality test") -> SimpleNamespace:
    return SimpleNamespace(input={"query": query})


def test_evaluator_is_deterministic_and_breaks_ties_lexicographically(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Analyzer:
        def __init__(self, _config: dict) -> None:
            pass

        def analyze(self, query: str) -> dict:
            calls.append(query)
            return {"provider_scores": {"serper": 4.0, "linkup": 4.0}}

    monkeypatch.setattr(shadow, "QueryAnalyzer", Analyzer)
    plan = ProviderPlan(("serper", "linkup"), "serper")

    first = shadow.evaluate_shadow_policy(_request(), plan, {})
    second = shadow.evaluate_shadow_policy(_request(), plan, {})

    assert first == second == {
        "observed": True,
        "policy_id": "shadow-quality",
        "policy_revision": "3.1",
        "selected_provider": "serper",
        "shadow_provider": "linkup",
        "agreement": False,
        "affected_execution": False,
    }
    assert calls == ["quality test", "quality test"]


def test_evaluator_restricts_ranking_to_the_classic_candidate_pool(monkeypatch) -> None:
    class Analyzer:
        def __init__(self, _config: dict) -> None:
            pass

        def analyze(self, _query: str) -> dict:
            return {
                "provider_scores": {
                    "exa": 100.0,
                    "linkup": 7.0,
                    "serper": 1.0,
                }
            }

    monkeypatch.setattr(shadow, "QueryAnalyzer", Analyzer)
    plan = ProviderPlan(("serper", "linkup"), "serper")

    observation = shadow.evaluate_shadow_policy(_request(), plan, {})

    assert observation["shadow_provider"] == "linkup"
    assert observation["shadow_provider"] in plan.candidate_order


def test_evaluator_is_side_effect_free_for_plan_and_config(monkeypatch) -> None:
    class Analyzer:
        def __init__(self, _config: dict) -> None:
            pass

        def analyze(self, _query: str) -> dict:
            return {"provider_scores": {"serper": 1.0}}

    monkeypatch.setattr(shadow, "QueryAnalyzer", Analyzer)
    config = {"auto_routing": {"provider_priority": ["linkup", "serper"]}}
    plan = ProviderPlan(
        ("serper",),
        "serper",
        routing_metadata={"analysis_summary": {"routing_class": "policy_pdf"}},
    )
    original_config = deepcopy(config)
    original_metadata = deepcopy(plan.routing_metadata)

    observation = shadow.evaluate_shadow_policy(_request(), plan, config)

    assert observation["agreement"] is True
    assert config == original_config
    assert plan.routing_metadata == original_metadata
