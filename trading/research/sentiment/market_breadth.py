from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from config.settings import settings
from trading.research.sentiment.policy import MARKET_BREADTH_VERSION
from trading.research.sentiment.repository import SentimentRepository
from trading.research.sentiment.schemas import (
    MarketBreadthSnapshot,
    SentimentRiskFlag,
)
from trading.schemas import AnalysisMode


def _stable_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _limit_ratio(symbol: str) -> float:
    code = symbol.split(".", 1)[0]
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("4", "8", "920")):
        return 0.30
    return 0.10


def _temperature(score: float) -> str:
    if score >= 0.6:
        return "HOT"
    if score >= 0.2:
        return "WARM"
    if score <= -0.6:
        return "COLD"
    if score <= -0.2:
        return "COOL"
    return "NEUTRAL"


class MarketBreadthService:
    """Deterministic breadth calculation; it never invokes a model."""

    def __init__(
        self,
        repository: SentimentRepository | None = None,
    ) -> None:
        self.repository = repository or SentimentRepository()

    def calculate(
        self,
        *,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
        persist: bool,
    ) -> MarketBreadthSnapshot:
        rows = self.repository.market_daily_records(
            data_cutoff=data_cutoff
        )
        by_symbol: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
        for row in rows:
            payload = row["payload"]
            trade_value = str(payload.get("trade_date") or "")
            try:
                trade_date = datetime.strptime(
                    trade_value.replace("-", ""),
                    "%Y%m%d",
                ).date()
            except ValueError:
                trade_date = row["event_time"].date()
            current = by_symbol[row["symbol"]].get(trade_date)
            if current is None or row["data_cutoff"] < current["data_cutoff"]:
                by_symbol[row["symbol"]][trade_date] = row

        all_dates = sorted(
            {day for values in by_symbol.values() for day in values}
        )
        generated_at = datetime.now().astimezone()
        if not all_dates:
            input_hash = _stable_hash(
                {
                    "cutoff": data_cutoff.isoformat(),
                    "version": MARKET_BREADTH_VERSION,
                    "rows": [],
                }
            )
            snapshot = MarketBreadthSnapshot(
                market_snapshot_id="smb_" + input_hash[:32],
                analysis_mode=analysis_mode,
                data_cutoff=data_cutoff,
                generated_at=generated_at,
                score=0,
                confidence=0,
                universe_size=0,
                missing_fields=[
                    "market_daily_records",
                    "sector_advance_ratios",
                    "sector_diffusion",
                ],
                risk_flags=[
                    SentimentRiskFlag.DATA_GAP,
                    SentimentRiskFlag.PARTIAL_UNIVERSE,
                ],
                evidence_ids=[],
                input_snapshot_hash=input_hash,
                algorithm_version=MARKET_BREADTH_VERSION,
            )
            return (
                self.repository.save_market_snapshot(snapshot)
                if persist
                else snapshot
            )

        latest_date = all_dates[-1]
        current_rows: list[tuple[dict[str, Any], float, float, float]] = []
        missing_fields: set[str] = {
            "sector_advance_ratios",
            "sector_diffusion",
        }
        for symbol, dated in by_symbol.items():
            current = dated.get(latest_date)
            if current is None:
                continue
            previous_dates = sorted(day for day in dated if day < latest_date)
            if not previous_dates:
                continue
            previous = dated[previous_dates[-1]]
            close = _number(current["payload"], "close")
            previous_close = _number(previous["payload"], "close")
            high = _number(current["payload"], "high")
            if close is None or previous_close is None or previous_close <= 0:
                continue
            current_rows.append(
                (
                    current,
                    close / previous_close - 1,
                    high / previous_close - 1 if high is not None else float("nan"),
                    _limit_ratio(symbol),
                )
            )

        advances = sum(change > 1e-8 for _, change, _, _ in current_rows)
        declines = sum(change < -1e-8 for _, change, _, _ in current_rows)
        flats = len(current_rows) - advances - declines
        limit_ups = sum(
            change >= ratio - 0.005
            for _, change, _, ratio in current_rows
        )
        limit_downs = sum(
            change <= -ratio + 0.005
            for _, change, _, ratio in current_rows
        )
        broken = sum(
            high_change >= ratio - 0.005
            and change < ratio - 0.005
            for _, change, high_change, ratio in current_rows
        )
        touched_limit_up = limit_ups + broken
        broken_rate = (
            broken / touched_limit_up if touched_limit_up else 0.0
        )
        amounts = [
            amount
            for row, _, _, _ in current_rows
            if (amount := _number(row["payload"], "amount")) is not None
        ]
        total_amount = sum(amounts) if amounts else None
        if total_amount is None:
            missing_fields.add("total_amount")
            missing_fields.add("amount_vs_20d_average")
            missing_fields.add("advance_amount_ratio")
            missing_fields.add("decline_amount_ratio")

        daily_amounts: list[float] = []
        for day in all_dates[-21:-1]:
            values = [
                _number(dated[day]["payload"], "amount")
                for dated in by_symbol.values()
                if day in dated
            ]
            known = [value for value in values if value is not None]
            if known:
                daily_amounts.append(sum(known))
        amount_vs_average = None
        if total_amount is not None and daily_amounts:
            average = sum(daily_amounts) / len(daily_amounts)
            amount_vs_average = total_amount / average if average > 0 else None
        elif total_amount is not None:
            missing_fields.add("amount_vs_20d_average")

        advance_amount = sum(
            amount
            for row, change, _, _ in current_rows
            if change > 0
            and (amount := _number(row["payload"], "amount")) is not None
        )
        decline_amount = sum(
            amount
            for row, change, _, _ in current_rows
            if change < 0
            and (amount := _number(row["payload"], "amount")) is not None
        )
        known_direction_amount = advance_amount + decline_amount
        advance_amount_ratio = (
            advance_amount / known_direction_amount
            if known_direction_amount > 0
            else None
        )
        decline_amount_ratio = (
            decline_amount / known_direction_amount
            if known_direction_amount > 0
            else None
        )

        max_streak = 0
        for symbol, dated in by_symbol.items():
            streak = 0
            dates = sorted(day for day in dated if day <= latest_date)
            for index in range(len(dates) - 1, 0, -1):
                close = _number(dated[dates[index]]["payload"], "close")
                previous_close = _number(
                    dated[dates[index - 1]]["payload"],
                    "close",
                )
                if close is None or previous_close is None or previous_close <= 0:
                    break
                if close / previous_close - 1 >= _limit_ratio(symbol) - 0.005:
                    streak += 1
                else:
                    break
            max_streak = max(max_streak, streak)

        universe = len(current_rows)
        breadth = (
            (advances - declines) / universe if universe else 0.0
        )
        limit_component = (
            (limit_ups - limit_downs) / universe if universe else 0.0
        )
        amount_component = (
            max(-1.0, min(1.0, amount_vs_average - 1))
            if amount_vs_average is not None
            else 0.0
        )
        score = max(
            -1.0,
            min(
                1.0,
                breadth * 0.6
                + limit_component * 0.25
                + amount_component * 0.15,
            ),
        )
        risk_flags = [SentimentRiskFlag.PARTIAL_UNIVERSE]
        coverage = universe / settings.sentiment_expected_market_universe_size
        confidence = min(
            settings.sentiment_market_partial_confidence_cap,
            0.2 + 0.8 * coverage,
        )
        evidence_ids = [
            row["canonical_record_id"]
            for row, _, _, _ in current_rows
        ]
        input_hash = _stable_hash(
            {
                "latest_date": latest_date.isoformat(),
                "records": sorted(evidence_ids),
                "cutoff": data_cutoff.isoformat(),
                "version": MARKET_BREADTH_VERSION,
            }
        )
        snapshot = MarketBreadthSnapshot(
            market_snapshot_id="smb_" + input_hash[:32],
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
            generated_at=generated_at,
            score=score,
            confidence=confidence,
            universe_size=universe,
            advances=advances,
            declines=declines,
            flats=flats,
            limit_ups=limit_ups,
            limit_downs=limit_downs,
            broken_limit_ups=broken,
            broken_limit_up_rate=broken_rate,
            max_limit_up_streak=max_streak,
            total_amount=total_amount,
            amount_vs_20d_average=amount_vs_average,
            advance_amount_ratio=advance_amount_ratio,
            decline_amount_ratio=decline_amount_ratio,
            sector_advance_ratios={},
            sector_diffusion=None,
            high_level_strength=(
                max(-1.0, min(1.0, (limit_ups - limit_downs) / universe))
                if universe
                else None
            ),
            temperature=_temperature(score),
            missing_fields=sorted(missing_fields),
            risk_flags=risk_flags,
            evidence_ids=evidence_ids,
            input_snapshot_hash=input_hash,
            algorithm_version=MARKET_BREADTH_VERSION,
        )
        return (
            self.repository.save_market_snapshot(snapshot)
            if persist
            else snapshot
        )


__all__ = ["MarketBreadthService"]
