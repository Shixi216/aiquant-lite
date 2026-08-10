from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from trading.research.sentiment.policy import SENTIMENT_EVALUATION_VERSION
from trading.research.sentiment.repository import SentimentRepository
from trading.research.sentiment.schemas import SentimentEvaluation


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


def _close(row: dict[str, Any]) -> float | None:
    value = row["payload"].get("close")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


class SentimentEvaluationService:
    """After-the-fact measurement; never feeds future returns into analysis."""

    def __init__(
        self,
        repository: SentimentRepository | None = None,
    ) -> None:
        self.repository = repository or SentimentRepository()

    def evaluate(
        self,
        *,
        snapshot_id: str,
        data_cutoff: datetime,
    ) -> SentimentEvaluation:
        snapshot = self.repository.get_symbol_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"sentiment snapshot not found: {snapshot_id}")
        if data_cutoff < snapshot.data_cutoff:
            raise ValueError(
                "evaluation cutoff must not precede snapshot cutoff"
            )
        rows = [
            row
            for row in self.repository.market_daily_records(
                data_cutoff=data_cutoff
            )
            if row["symbol"] == snapshot.symbol
        ]
        rows.sort(key=lambda row: row["event_time"])
        base_candidates = [
            row for row in rows if row["event_time"] <= snapshot.data_cutoff
        ]
        future = [
            row for row in rows if row["event_time"] > snapshot.data_cutoff
        ]
        base = _close(base_candidates[-1]) if base_candidates else None
        future_closes = [
            value
            for row in future
            if (value := _close(row)) is not None
        ]

        def result_at(index: int) -> float | None:
            if base is None or base <= 0 or len(future_closes) <= index:
                return None
            return future_closes[index] / base - 1

        returns = [result_at(index) for index in (0, 2, 4)]
        complete = base is not None and len(future_closes) >= 5
        missing = []
        for name, value in zip(
            ("return_1d", "return_3d", "return_5d"),
            returns,
            strict=True,
        ):
            if value is None:
                missing.append(name)
        if base is None:
            missing.append("base_close")
        evaluated_closes = future_closes[:5]
        max_rise = (
            max(value / base - 1 for value in evaluated_closes)
            if base and evaluated_closes
            else None
        )
        max_drawdown = (
            min(value / base - 1 for value in evaluated_closes)
            if base and evaluated_closes
            else None
        )
        was_limit_up = None
        was_limit_down = None
        if returns[0] is not None:
            ratio = (
                0.2
                if snapshot.symbol.startswith(("300", "301", "688", "689"))
                else 0.3
                if snapshot.symbol.startswith(("4", "8", "920"))
                else 0.1
            )
            was_limit_up = returns[0] >= ratio - 0.005
            was_limit_down = returns[0] <= -ratio + 0.005
        payload = {
            "snapshot_id": snapshot_id,
            "data_cutoff": data_cutoff.isoformat(),
            "future_evidence": [
                row["canonical_record_id"] for row in future[:5]
            ],
            "version": SENTIMENT_EVALUATION_VERSION,
        }
        input_hash = _stable_hash(payload)
        evaluation = SentimentEvaluation(
            evaluation_id="sev_" + input_hash[:32],
            snapshot_id=snapshot_id,
            symbol=snapshot.symbol,
            event_time=None,
            alert_or_snapshot_time=snapshot.generated_at,
            data_cutoff=data_cutoff,
            sentiment_score=snapshot.weighted_event_score,
            confidence=snapshot.confidence,
            return_1d=returns[0],
            return_3d=returns[1],
            return_5d=returns[2],
            max_rise=max_rise,
            max_drawdown=max_drawdown,
            was_suspended=None,
            was_limit_up=was_limit_up,
            was_limit_down=was_limit_down,
            data_complete=complete,
            evidence_ids=[
                row["canonical_record_id"] for row in future[:5]
            ],
            missing_fields=sorted(set(missing)),
            generated_at=datetime.now().astimezone(),
            algorithm_version=SENTIMENT_EVALUATION_VERSION,
        )
        return self.repository.save_evaluation(evaluation)


__all__ = ["SentimentEvaluationService"]
