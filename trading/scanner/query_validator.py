from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from trading.scanner.query_ast import (
    BooleanNode,
    ComparisonNode,
    ComparisonOperator,
    FilterNode,
    MembershipNode,
    RangeNode,
    ScannerField,
)


_BOOLEAN_FIELDS = {
    ScannerField.IS_ST,
    ScannerField.IS_SUSPENDED,
    ScannerField.ABOVE_SMA5,
    ScannerField.ABOVE_SMA10,
    ScannerField.ABOVE_SMA20,
    ScannerField.ABOVE_SMA60,
    ScannerField.MA_BULLISH,
    ScannerField.MA_BEARISH,
    ScannerField.BREAKOUT_HIGH_20D,
    ScannerField.BREAKDOWN_LOW_20D,
}
_TEXT_FIELDS = {
    ScannerField.SYMBOL,
    ScannerField.BOARD,
    ScannerField.INDUSTRY,
    ScannerField.MACD_STATE,
    ScannerField.RISK_FLAGS,
}
_PERCENT_FIELDS = {
    ScannerField.CHANGE_PCT,
    ScannerField.TURNOVER_RATE,
    ScannerField.AMPLITUDE,
    ScannerField.VOLATILITY_20D,
    ScannerField.AMOUNT_MARKET_PERCENTILE,
    ScannerField.COMPOSITE_CONFIDENCE,
}
_SCORE_FIELDS = {
    ScannerField.TECHNICAL_SCORE,
    ScannerField.FUNDAMENTAL_SCORE,
    ScannerField.SENTIMENT_SCORE,
    ScannerField.POLICY_NEWS_SCORE,
    ScannerField.CAPITAL_FLOW_SCORE,
    ScannerField.SHADOW_COMPOSITE_SCORE,
}
_NONNEGATIVE_FIELDS = {
    ScannerField.CURRENT_PRICE,
    ScannerField.CURRENT_VOLUME,
    ScannerField.AMOUNT,
    ScannerField.VOLUME_RATIO_5D,
    ScannerField.VOLUME_RATIO_20D,
    ScannerField.AMOUNT_RATIO_5D,
    ScannerField.AMOUNT_RATIO_20D,
    ScannerField.SMA5,
    ScannerField.SMA10,
    ScannerField.SMA20,
    ScannerField.SMA60,
    ScannerField.RSI14,
    ScannerField.CONSECUTIVE_UP_DAYS,
    ScannerField.CONSECUTIVE_DOWN_DAYS,
    ScannerField.LISTING_AGE_DAYS,
    ScannerField.FACTOR_COVERAGE_COUNT,
}
_INJECTION = re.compile(
    r"(?i)(?:\bselect\b|\bunion\b|\bdrop\b|\binsert\b|\bdelete\b|"
    r"\bupdate\b|--|/\*|\*/|;|\beval\b|\bexec\b|__import__|os\.)"
)


def ensure_safe_text(value: str) -> None:
    if _INJECTION.search(value):
        raise ValueError("query contains a forbidden SQL or Python token")


def _validate_scalar(field: ScannerField, value: Any) -> None:
    if field in _BOOLEAN_FIELDS:
        if not isinstance(value, bool):
            raise ValueError(f"{field.value} requires a boolean")
        return
    if field in _TEXT_FIELDS:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field.value} requires non-empty text")
        ensure_safe_text(value)
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field.value} requires a number")
    numeric = float(value)
    if field in _SCORE_FIELDS and not -1 <= numeric <= 1:
        raise ValueError(f"{field.value} must be between -1 and 1")
    if field in _PERCENT_FIELDS and not -2 <= numeric <= 10:
        raise ValueError(f"{field.value} is outside the safe range")
    if field in _NONNEGATIVE_FIELDS and numeric < 0:
        raise ValueError(f"{field.value} must not be negative")
    if field == ScannerField.FACTOR_COVERAGE_COUNT and numeric > 5:
        raise ValueError("factor_coverage_count must not exceed 5")
    if field == ScannerField.RSI14 and numeric > 100:
        raise ValueError("rsi14 must not exceed 100")
    if field == ScannerField.CURRENT_PRICE and numeric > 10_000_000:
        raise ValueError("current_price is outside the safe range")
    if field == ScannerField.AMOUNT and numeric > 1_000_000_000_000_000:
        raise ValueError("amount is outside the safe range")


def validate_filter(node: FilterNode) -> None:
    if isinstance(node, BooleanNode):
        for child in node.children:
            validate_filter(child)
        return
    if isinstance(node, ComparisonNode):
        _validate_scalar(node.field, node.value)
        if node.field in _TEXT_FIELDS and node.operator not in {
            ComparisonOperator.EQ,
            ComparisonOperator.NE,
        }:
            raise ValueError(f"{node.field.value} supports only EQ or NE")
        return
    if isinstance(node, RangeNode):
        _validate_scalar(node.field, node.minimum)
        _validate_scalar(node.field, node.maximum)
        if node.minimum > node.maximum:
            raise ValueError(f"{node.field.value} range is reversed")
        return
    if isinstance(node, MembershipNode):
        for value in node.values:
            _validate_scalar(node.field, value)
        return
    raise TypeError(f"unsupported scanner AST node: {type(node).__name__}")


def canonical_plan_payload(values: dict[str, Any]) -> dict[str, Any]:
    excluded = {"query_id", "plan_hash", "generated_at"}
    payload: dict[str, Any] = {}
    for key, value in values.items():
        if key in excluded:
            continue
        if hasattr(value, "model_dump"):
            payload[key] = value.model_dump(mode="json")
        elif isinstance(value, list):
            payload[key] = [
                item.model_dump(mode="json")
                if hasattr(item, "model_dump")
                else item.value
                if hasattr(item, "value")
                else item
                for item in value
            ]
        elif isinstance(value, datetime):
            payload[key] = value.isoformat()
        elif hasattr(value, "value"):
            payload[key] = value.value
        else:
            payload[key] = value
    return payload


def stable_plan_hash(values: dict[str, Any]) -> str:
    payload = canonical_plan_payload(values)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "canonical_plan_payload",
    "ensure_safe_text",
    "stable_plan_hash",
    "validate_filter",
]
