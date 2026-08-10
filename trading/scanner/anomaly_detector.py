from __future__ import annotations

from typing import Any

import pandas as pd

from trading.scanner.models import (
    ANOMALY_THRESHOLDS,
    PRICE_LIMIT_RATES,
    AnomalyThresholds,
    AnomalyType,
    ScannerRiskFlag,
)


class ScannerAnomalyDetector:
    def __init__(
        self,
        thresholds: AnomalyThresholds = ANOMALY_THRESHOLDS,
    ) -> None:
        self.thresholds = thresholds

    def _detect_row(
        self,
        row: Any,
    ) -> tuple[list[AnomalyType], dict[str, float | str], list[ScannerRiskFlag]]:
        anomalies: list[AnomalyType] = []
        evidence: dict[str, float | str] = {}
        risks: list[ScannerRiskFlag] = []
        if bool(row.is_suspended):
            return (
                [],
                {},
                [
                    ScannerRiskFlag.POSSIBLE_SUSPENSION,
                    ScannerRiskFlag.SCANNER_DATA_GAP,
                ],
            )

        def number(value: Any) -> float | None:
            return None if value is None or pd.isna(value) else float(value)

        change = number(row.change_pct)
        volume_ratio = number(row.volume_ratio_5d)
        amount_ratio = number(row.amount_ratio_5d)
        turnover = number(row.turnover_rate)
        volatility = number(row.volatility_20d)
        gap = number(row.gap_pct)
        amount = number(row.amount)
        if change is not None:
            evidence["change_pct"] = change
            if change >= self.thresholds.price_surge:
                anomalies.append(AnomalyType.PRICE_SURGE)
            if change <= self.thresholds.price_drop:
                anomalies.append(AnomalyType.PRICE_DROP)
        if volume_ratio is not None:
            evidence["volume_ratio_5d"] = volume_ratio
            if volume_ratio >= self.thresholds.volume_spike:
                anomalies.append(AnomalyType.VOLUME_SPIKE)
        if amount_ratio is not None:
            evidence["amount_ratio_5d"] = amount_ratio
            if amount_ratio >= self.thresholds.amount_spike:
                anomalies.append(AnomalyType.AMOUNT_SPIKE)
            if amount_ratio <= self.thresholds.liquidity_drop_ratio:
                anomalies.append(AnomalyType.LIQUIDITY_DROP)
        if turnover is not None:
            evidence["turnover_rate"] = turnover
            if turnover >= self.thresholds.high_turnover:
                anomalies.append(AnomalyType.HIGH_TURNOVER)
        if amount is not None and amount < self.thresholds.low_liquidity_amount:
            risks.append(ScannerRiskFlag.LOW_LIQUIDITY)
        state_map = {
            "PRICE_UP_VOLUME_UP": AnomalyType.PRICE_UP_VOLUME_UP,
            "PRICE_UP_VOLUME_DOWN": AnomalyType.PRICE_UP_VOLUME_DOWN,
            "PRICE_DOWN_VOLUME_UP": AnomalyType.PRICE_DOWN_VOLUME_UP,
            "PRICE_DOWN_VOLUME_DOWN": AnomalyType.PRICE_DOWN_VOLUME_DOWN,
        }
        state = state_map.get(row.price_volume_state)
        if state is not None:
            anomalies.append(state)
        if (
            row.breakout_high_20d is not None
            and not pd.isna(row.breakout_high_20d)
            and bool(row.breakout_high_20d)
        ):
            anomalies.append(AnomalyType.BREAKOUT_HIGH)
        if (
            row.breakdown_low_20d is not None
            and not pd.isna(row.breakdown_low_20d)
            and bool(row.breakdown_low_20d)
        ):
            anomalies.append(AnomalyType.BREAKDOWN_LOW)
        if volatility is not None:
            evidence["volatility_20d"] = volatility
            if volatility >= self.thresholds.volatility_spike:
                anomalies.append(AnomalyType.VOLATILITY_SPIKE)
                risks.append(ScannerRiskFlag.EXTREME_VOLATILITY)
        if gap is not None:
            evidence["gap_pct"] = gap
            if gap >= self.thresholds.gap:
                anomalies.append(AnomalyType.GAP_UP)
            if gap <= -self.thresholds.gap:
                anomalies.append(AnomalyType.GAP_DOWN)

        limit_rate = PRICE_LIMIT_RATES.get(str(row.price_limit_type))
        previous_close = number(row.previous_close)
        current = number(row.current_price)
        if limit_rate is None:
            risks.append(ScannerRiskFlag.PRICE_LIMIT_RULE_UNKNOWN)
        elif previous_close is not None and previous_close > 0 and current is not None:
            actual_change = current / previous_close - 1
            distance = min(
                abs(actual_change - limit_rate),
                abs(actual_change + limit_rate),
            )
            evidence["price_limit_rate"] = limit_rate
            if abs(actual_change - limit_rate) <= self.thresholds.limit_tolerance:
                anomalies.append(AnomalyType.PRICE_LIMIT_UP)
            elif abs(actual_change + limit_rate) <= self.thresholds.limit_tolerance:
                anomalies.append(AnomalyType.PRICE_LIMIT_DOWN)
            elif distance <= self.thresholds.near_limit_distance:
                anomalies.append(AnomalyType.NEAR_PRICE_LIMIT)

        anomalies = list(dict.fromkeys(anomalies))
        if len(anomalies) >= self.thresholds.confluence_count:
            anomalies.append(AnomalyType.MULTI_SIGNAL_CONFLUENCE)
            risks.append(ScannerRiskFlag.MULTI_SIGNAL_CONFLUENCE)
        return (
            list(dict.fromkeys(anomalies)),
            evidence,
            list(dict.fromkeys(risks)),
        )

    def detect(self, frame: pd.DataFrame) -> pd.DataFrame:
        output = frame.copy()
        values = [
            self._detect_row(row) for row in output.itertuples(index=False)
        ]
        output["anomaly_types"] = [item[0] for item in values]
        output["anomaly_evidence"] = [item[1] for item in values]
        output["local_risk_flags"] = [item[2] for item in values]
        return output


__all__ = ["ScannerAnomalyDetector"]
