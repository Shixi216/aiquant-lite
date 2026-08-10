from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings
from trading.schemas import AnalysisMode


CAPITAL_FLOW_ALGORITHM_VERSION = "capital-flow-local-score-v1"
CAPITAL_FLOW_AGGREGATION_VERSION = "capital-flow-aggregate-v1"
CAPITAL_FLOW_EVALUATION_VERSION = "capital-flow-evaluation-v1"
CAPITAL_FLOW_MAPPING_VERSION = "stock-basic-industry-v1"


@dataclass(frozen=True)
class CapitalFlowModePolicy:
    allow_model_calls: bool
    allow_external_fetch: bool
    allow_per_symbol_fetch: bool
    persist_snapshot: bool
    persist_factor: bool
    strict_point_in_time: bool
    use_existing_snapshot: bool
    shadow_mode: bool = True
    formal_strategy_weight: float = 0.0


_MODE_POLICIES = {
    AnalysisMode.SCREENING: CapitalFlowModePolicy(
        allow_model_calls=False,
        allow_external_fetch=False,
        allow_per_symbol_fetch=False,
        persist_snapshot=False,
        persist_factor=False,
        strict_point_in_time=True,
        use_existing_snapshot=True,
    ),
    AnalysisMode.RESEARCH: CapitalFlowModePolicy(
        allow_model_calls=False,
        allow_external_fetch=True,
        allow_per_symbol_fetch=True,
        persist_snapshot=True,
        persist_factor=True,
        strict_point_in_time=True,
        use_existing_snapshot=False,
    ),
    AnalysisMode.DECISION: CapitalFlowModePolicy(
        allow_model_calls=False,
        allow_external_fetch=False,
        allow_per_symbol_fetch=False,
        persist_snapshot=True,
        persist_factor=True,
        strict_point_in_time=True,
        use_existing_snapshot=False,
    ),
}


def policy_for(mode: AnalysisMode) -> CapitalFlowModePolicy:
    return _MODE_POLICIES[mode]


def scoring_weights() -> dict[str, float]:
    return {
        "volume": settings.capital_flow_volume_weight,
        "amount": settings.capital_flow_amount_weight,
        "turnover": settings.capital_flow_turnover_weight,
        "price_volume": settings.capital_flow_price_volume_weight,
        "financing": settings.capital_flow_financing_weight,
        "sector_flow": settings.capital_flow_sector_flow_weight,
        "liquidity": settings.capital_flow_liquidity_weight,
    }


__all__ = [
    "CAPITAL_FLOW_AGGREGATION_VERSION",
    "CAPITAL_FLOW_ALGORITHM_VERSION",
    "CAPITAL_FLOW_EVALUATION_VERSION",
    "CAPITAL_FLOW_MAPPING_VERSION",
    "CapitalFlowModePolicy",
    "policy_for",
    "scoring_weights",
]
