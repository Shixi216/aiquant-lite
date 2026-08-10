from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from trading.research.policy_news.policy import (
    POLICY_NEWS_EVALUATION_VERSION,
)
from trading.research.policy_news.repository import PolicyNewsRepository
from trading.research.policy_news.schemas import (
    ImplementationStatus,
    PolicyNewsEvaluation,
)


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


class PolicyNewsEvaluationService:
    """After-the-fact evaluation; future returns never feed analysis."""

    def __init__(
        self,
        repository: PolicyNewsRepository | None = None,
    ) -> None:
        self.repository = repository or PolicyNewsRepository()

    def evaluate(
        self,
        *,
        snapshot_id: str,
        data_cutoff: datetime,
        actually_implemented: bool | None = None,
        terminated_or_retracted: bool | None = None,
    ) -> PolicyNewsEvaluation:
        snapshot = self.repository.get_symbol_snapshot(snapshot_id)
        if snapshot is None:
            sector_snapshot = self.repository.get_sector_snapshot(snapshot_id)
            if sector_snapshot is None:
                raise KeyError(f"policy snapshot not found: {snapshot_id}")
            evaluation = PolicyNewsEvaluation(
                evaluation_id="pne_" + _stable_hash(
                    {
                        "snapshot": snapshot_id,
                        "cutoff": data_cutoff.isoformat(),
                        "version": POLICY_NEWS_EVALUATION_VERSION,
                    }
                )[:32],
                snapshot_id=snapshot_id,
                sector=sector_snapshot.sector,
                implementation_status_at_analysis=(
                    ImplementationStatus.UNKNOWN
                ),
                data_cutoff=data_cutoff,
                policy_news_score=sector_snapshot.weighted_policy_score,
                confidence=sector_snapshot.confidence,
                actually_implemented=actually_implemented,
                terminated_or_retracted=terminated_or_retracted,
                data_complete=False,
                missing_fields=[
                    "sector_return_series",
                    "return_1d",
                    "return_3d",
                    "return_5d",
                    "return_20d",
                ],
                generated_at=datetime.now().astimezone(),
                algorithm_version=POLICY_NEWS_EVALUATION_VERSION,
            )
            return self.repository.save_evaluation(evaluation)
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

        returns = [result_at(index) for index in (0, 2, 4, 19)]
        missing = [
            name
            for name, value in zip(
                (
                    "return_1d",
                    "return_3d",
                    "return_5d",
                    "return_20d",
                ),
                returns,
                strict=True,
            )
            if value is None
        ]
        if base is None:
            missing.append("base_close")
        evaluated = future_closes[:20]
        max_rise = (
            max(value / base - 1 for value in evaluated)
            if base and evaluated
            else None
        )
        max_drawdown = (
            min(value / base - 1 for value in evaluated)
            if base and evaluated
            else None
        )
        analyses = self.repository.list_analyses(
            symbol=snapshot.symbol,
            data_cutoff=snapshot.data_cutoff,
        )
        leading = max(
            analyses,
            key=lambda item: abs(item.policy_news_score),
            default=None,
        )
        evidence_ids = [
            row["canonical_record_id"] for row in future[:20]
        ]
        input_hash = _stable_hash(
            {
                "snapshot": snapshot_id,
                "cutoff": data_cutoff.isoformat(),
                "future_evidence": evidence_ids,
                "version": POLICY_NEWS_EVALUATION_VERSION,
            }
        )
        evaluation = PolicyNewsEvaluation(
            evaluation_id="pne_" + input_hash[:32],
            snapshot_id=snapshot_id,
            symbol=snapshot.symbol,
            event_time=leading.event_time if leading else None,
            publication_time=(
                leading.publication_time if leading else None
            ),
            implementation_status_at_analysis=(
                leading.implementation_status
                if leading
                else ImplementationStatus.UNKNOWN
            ),
            data_cutoff=data_cutoff,
            policy_news_score=snapshot.weighted_policy_score,
            confidence=snapshot.confidence,
            return_1d=returns[0],
            return_3d=returns[1],
            return_5d=returns[2],
            return_20d=returns[3],
            max_rise=max_rise,
            max_drawdown=max_drawdown,
            actually_implemented=actually_implemented,
            terminated_or_retracted=terminated_or_retracted,
            data_complete=base is not None and len(future_closes) >= 20,
            evidence_ids=evidence_ids,
            missing_fields=sorted(set(missing)),
            generated_at=datetime.now().astimezone(),
            algorithm_version=POLICY_NEWS_EVALUATION_VERSION,
        )
        return self.repository.save_evaluation(evaluation)


__all__ = ["PolicyNewsEvaluationService"]
