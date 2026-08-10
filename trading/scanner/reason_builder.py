from __future__ import annotations

from typing import Any

import pandas as pd


def build_reason_codes(row: Any) -> list[str]:
    reasons = ["QUERY_HARD_FILTER_MATCH"]
    reasons.extend(f"ANOMALY_{item.value}" for item in row.anomaly_types)
    if row.technical_score is not None and not pd.isna(row.technical_score):
        reasons.append(
            "TECHNICAL_SCORE_POSITIVE"
            if row.technical_score > 0
            else "TECHNICAL_SCORE_NON_POSITIVE"
        )
    if row.capital_flow_score is not None and not pd.isna(row.capital_flow_score):
        reasons.append(
            "CAPITAL_FLOW_SCORE_POSITIVE"
            if row.capital_flow_score > 0
            else "CAPITAL_FLOW_SCORE_NON_POSITIVE"
        )
    if (
        row.above_sma20 is not None
        and not pd.isna(row.above_sma20)
        and bool(row.above_sma20)
    ):
        reasons.append("ABOVE_SMA20")
    if (
        row.ma_bullish is not None
        and not pd.isna(row.ma_bullish)
        and bool(row.ma_bullish)
    ):
        reasons.append("MA_BULLISH")
    if row.factor_coverage_count >= 2:
        reasons.append("FACTOR_COVERAGE_AT_LEAST_2_OF_5")
    return list(dict.fromkeys(reasons))


__all__ = ["build_reason_codes"]
