from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


SCANNER_VERSION = "market-scanner-v1"
LOCAL_PARSER_VERSION = "scanner-local-zh-v1"
MODEL_PARSER_VERSION = "scanner-model-json-v1"


class MissingDataPolicy(StrEnum):
    EXCLUDE = "EXCLUDE"
    INCLUDE_WITH_FLAG = "INCLUDE_WITH_FLAG"
    IGNORE_FILTER = "IGNORE_FILTER"
    REQUIRE_CLARIFICATION = "REQUIRE_CLARIFICATION"


class FreshnessPolicy(StrEnum):
    FLAG_STALE = "FLAG_STALE"
    EXCLUDE_STALE = "EXCLUDE_STALE"
    REQUIRE_FRESH = "REQUIRE_FRESH"


class ParserType(StrEnum):
    LOCAL = "LOCAL"
    MODEL = "MODEL"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class ResearchStatus(StrEnum):
    SCREEN_FLAG_ONLY = "SCREEN_FLAG_ONLY"
    RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"
    NEEDS_DATA = "NEEDS_DATA"


class AnomalyType(StrEnum):
    PRICE_SURGE = "PRICE_SURGE"
    PRICE_DROP = "PRICE_DROP"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    AMOUNT_SPIKE = "AMOUNT_SPIKE"
    HIGH_TURNOVER = "HIGH_TURNOVER"
    PRICE_UP_VOLUME_UP = "PRICE_UP_VOLUME_UP"
    PRICE_UP_VOLUME_DOWN = "PRICE_UP_VOLUME_DOWN"
    PRICE_DOWN_VOLUME_UP = "PRICE_DOWN_VOLUME_UP"
    PRICE_DOWN_VOLUME_DOWN = "PRICE_DOWN_VOLUME_DOWN"
    BREAKOUT_HIGH = "BREAKOUT_HIGH"
    BREAKDOWN_LOW = "BREAKDOWN_LOW"
    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"
    GAP_UP = "GAP_UP"
    GAP_DOWN = "GAP_DOWN"
    NEAR_PRICE_LIMIT = "NEAR_PRICE_LIMIT"
    PRICE_LIMIT_UP = "PRICE_LIMIT_UP"
    PRICE_LIMIT_DOWN = "PRICE_LIMIT_DOWN"
    LIQUIDITY_DROP = "LIQUIDITY_DROP"
    MULTI_SIGNAL_CONFLUENCE = "MULTI_SIGNAL_CONFLUENCE"


class ScannerRiskFlag(StrEnum):
    SCANNER_DATA_GAP = "SCANNER_DATA_GAP"
    PARTIAL_UNIVERSE = "PARTIAL_UNIVERSE"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    QUERY_AMBIGUOUS = "QUERY_AMBIGUOUS"
    QUERY_FIELD_UNSUPPORTED = "QUERY_FIELD_UNSUPPORTED"
    QUERY_VALUE_INVALID = "QUERY_VALUE_INVALID"
    MISSING_FILTER_FIELD = "MISSING_FILTER_FIELD"
    LOW_FACTOR_COVERAGE = "LOW_FACTOR_COVERAGE"
    LOW_COMPOSITE_CONFIDENCE = "LOW_COMPOSITE_CONFIDENCE"
    PRICE_LIMIT_RULE_UNKNOWN = "PRICE_LIMIT_RULE_UNKNOWN"
    POSSIBLE_SUSPENSION = "POSSIBLE_SUSPENSION"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    EXTREME_VOLATILITY = "EXTREME_VOLATILITY"
    MULTI_SIGNAL_CONFLUENCE = "MULTI_SIGNAL_CONFLUENCE"
    MODEL_PARSER_FAILED = "MODEL_PARSER_FAILED"
    MODE_RESTRICTION = "MODE_RESTRICTION"
    NOT_A_TRADE_RECOMMENDATION = "NOT_A_TRADE_RECOMMENDATION"


BOARD_ALIASES: dict[str, str] = {
    "沪市主板": "SH_MAIN",
    "上证主板": "SH_MAIN",
    "深市主板": "SZ_MAIN",
    "深证主板": "SZ_MAIN",
    "创业板": "CHINEXT",
    "科创板": "STAR",
    "北交所": "BEIJING",
    "北京证券交易所": "BEIJING",
}

PRICE_LIMIT_RATES: dict[str, float] = {
    "MAIN_10_PERCENT": 0.10,
    "GROWTH_20_PERCENT": 0.20,
    "BEIJING_30_PERCENT": 0.30,
    "ST_5_PERCENT": 0.05,
}


@dataclass(frozen=True)
class AnomalyThresholds:
    price_surge: float = 0.05
    price_drop: float = -0.05
    volume_spike: float = 2.0
    amount_spike: float = 2.0
    high_turnover: float = 0.10
    price_direction: float = 0.01
    volume_direction: float = 0.20
    volatility_spike: float = 0.60
    gap: float = 0.03
    near_limit_distance: float = 0.005
    limit_tolerance: float = 0.005
    liquidity_drop_ratio: float = 0.50
    low_liquidity_amount: float = 20_000_000.0
    confluence_count: int = 3


@dataclass(frozen=True)
class RankingWeights:
    query_match: float = 0.20
    anomaly_strength: float = 0.20
    technical: float = 0.15
    capital_flow: float = 0.15
    shadow_composite: float = 0.05
    factor_coverage: float = 0.10
    confidence: float = 0.05
    liquidity: float = 0.07
    freshness: float = 0.03
    maximum_risk_penalty: float = 0.25


ANOMALY_THRESHOLDS = AnomalyThresholds()
RANKING_WEIGHTS = RankingWeights()


__all__ = [
    "ANOMALY_THRESHOLDS",
    "BOARD_ALIASES",
    "LOCAL_PARSER_VERSION",
    "MODEL_PARSER_VERSION",
    "PRICE_LIMIT_RATES",
    "RANKING_WEIGHTS",
    "SCANNER_VERSION",
    "AnomalyThresholds",
    "AnomalyType",
    "FreshnessPolicy",
    "MissingDataPolicy",
    "ParserType",
    "RankingWeights",
    "ResearchStatus",
    "ScannerRiskFlag",
    "SortDirection",
]
