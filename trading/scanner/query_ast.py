from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from trading.scanner.models import SortDirection


Scalar = bool | float | int | str


class ScannerField(StrEnum):
    SYMBOL = "symbol"
    BOARD = "board"
    INDUSTRY = "industry"
    IS_ST = "is_st"
    IS_SUSPENDED = "is_suspended"
    LISTING_AGE_DAYS = "listing_age_days"
    CURRENT_PRICE = "current_price"
    CURRENT_VOLUME = "current_volume"
    CHANGE_PCT = "change_pct"
    AMOUNT = "amount"
    TURNOVER_RATE = "turnover_rate"
    AMPLITUDE = "amplitude"
    VOLUME_RATIO_5D = "volume_ratio_5d"
    VOLUME_RATIO_20D = "volume_ratio_20d"
    AMOUNT_RATIO_5D = "amount_ratio_5d"
    AMOUNT_RATIO_20D = "amount_ratio_20d"
    AMOUNT_MARKET_PERCENTILE = "amount_market_percentile"
    SMA5 = "sma5"
    SMA10 = "sma10"
    SMA20 = "sma20"
    SMA60 = "sma60"
    ABOVE_SMA5 = "above_sma5"
    ABOVE_SMA10 = "above_sma10"
    ABOVE_SMA20 = "above_sma20"
    ABOVE_SMA60 = "above_sma60"
    MA_BULLISH = "ma_bullish"
    MA_BEARISH = "ma_bearish"
    RSI14 = "rsi14"
    MACD_STATE = "macd_state"
    BREAKOUT_HIGH_20D = "breakout_high_20d"
    BREAKDOWN_LOW_20D = "breakdown_low_20d"
    CONSECUTIVE_UP_DAYS = "consecutive_up_days"
    CONSECUTIVE_DOWN_DAYS = "consecutive_down_days"
    VOLATILITY_20D = "volatility_20d"
    TECHNICAL_SCORE = "technical_score"
    FUNDAMENTAL_SCORE = "fundamental_score"
    SENTIMENT_SCORE = "sentiment_score"
    POLICY_NEWS_SCORE = "policy_news_score"
    CAPITAL_FLOW_SCORE = "capital_flow_score"
    SHADOW_COMPOSITE_SCORE = "shadow_composite_score"
    COMPOSITE_CONFIDENCE = "composite_confidence"
    FACTOR_COVERAGE_COUNT = "factor_coverage_count"
    RISK_FLAGS = "risk_flags"


class ComparisonOperator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"


class BooleanOperator(StrEnum):
    AND = "AND"
    OR = "OR"


class MembershipOperator(StrEnum):
    IN = "IN"
    NOT_IN = "NOT_IN"
    CONTAINS = "CONTAINS"
    EXCLUDES = "EXCLUDES"


class AstModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComparisonNode(AstModel):
    node_type: Literal["COMPARISON"] = "COMPARISON"
    field: ScannerField
    operator: ComparisonOperator
    value: Scalar


class RangeNode(AstModel):
    node_type: Literal["RANGE"] = "RANGE"
    field: ScannerField
    minimum: float | int
    maximum: float | int
    include_minimum: bool = True
    include_maximum: bool = True


class MembershipNode(AstModel):
    node_type: Literal["MEMBERSHIP"] = "MEMBERSHIP"
    field: ScannerField
    operator: MembershipOperator
    values: list[Scalar] = Field(min_length=1, max_length=100)


class BooleanNode(AstModel):
    node_type: Literal["BOOLEAN"] = "BOOLEAN"
    operator: BooleanOperator
    children: list["FilterNode"] = Field(min_length=2, max_length=50)


FilterNode = Annotated[
    ComparisonNode | RangeNode | MembershipNode | BooleanNode,
    Field(discriminator="node_type"),
]
BooleanNode.model_rebuild()


class SortNode(AstModel):
    field: ScannerField
    direction: SortDirection = SortDirection.DESC


class LimitNode(AstModel):
    limit: int = Field(ge=10, le=30)


__all__ = [
    "BooleanNode",
    "BooleanOperator",
    "ComparisonNode",
    "ComparisonOperator",
    "FilterNode",
    "LimitNode",
    "MembershipNode",
    "MembershipOperator",
    "RangeNode",
    "ScannerField",
    "SortNode",
]
