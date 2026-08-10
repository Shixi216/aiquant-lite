from __future__ import annotations

from datetime import datetime

from trading.research.capital_flow.aggregator import stable_hash
from trading.research.capital_flow.policy import CAPITAL_FLOW_EVALUATION_VERSION
from trading.research.capital_flow.repository import CapitalFlowRepository
from trading.research.capital_flow.schemas import CapitalFlowEvaluation


class CapitalFlowEvaluationService:
    def __init__(
        self,
        repository: CapitalFlowRepository | None = None,
    ) -> None:
        self.repository = repository or CapitalFlowRepository()

    def evaluate(
        self,
        *,
        snapshot_id: str,
        data_cutoff: datetime,
    ) -> CapitalFlowEvaluation:
        snapshot = self.repository.get_symbol_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"Capital-flow snapshot not found: {snapshot_id}")
        if data_cutoff < snapshot.data_cutoff:
            raise ValueError("evaluation cutoff must not precede snapshot cutoff")
        bars = self.repository.market_bars(
            data_cutoff=data_cutoff,
            symbols=[snapshot.symbol],
        ).get(snapshot.symbol, [])
        ordered = sorted(bars, key=lambda item: item.event_time)
        base_index = next(
            (
                index
                for index, bar in enumerate(ordered)
                if bar.event_time <= snapshot.data_cutoff
            ),
            None,
        )
        if base_index is None:
            raise ValueError("snapshot price evidence is unavailable")
        base_index = max(
            index
            for index, bar in enumerate(ordered)
            if bar.event_time <= snapshot.data_cutoff
        )
        base = ordered[base_index]
        future = ordered[base_index + 1 :]
        closes = [bar.close for bar in future if bar.close is not None]

        def forward_return(days: int) -> float | None:
            if base.close is None or base.close <= 0 or len(closes) < days:
                return None
            return closes[days - 1] / base.close - 1

        returns = {days: forward_return(days) for days in (1, 3, 5, 20)}
        path_returns = (
            [value / base.close - 1 for value in closes]
            if base.close is not None and base.close > 0
            else []
        )
        missing = [
            f"return_{days}d"
            for days, value in returns.items()
            if value is None
        ]
        last = future[0] if future else None
        return1 = returns[1]
        limit_ratio = 0.2 if snapshot.symbol.startswith(("300", "688")) else 0.1
        generated_at = datetime.now().astimezone()
        identity = stable_hash(
            {
                "snapshot_id": snapshot_id,
                "algorithm": CAPITAL_FLOW_EVALUATION_VERSION,
            }
        )
        evaluation = CapitalFlowEvaluation(
            evaluation_id="cfe_" + identity[:32],
            snapshot_id=snapshot_id,
            snapshot_time=snapshot.data_cutoff,
            symbol=snapshot.symbol,
            data_cutoff=data_cutoff,
            capital_score=snapshot.score,
            confidence=snapshot.confidence,
            price_volume_state=snapshot.price_volume_state,
            volume_ratio_20d=snapshot.volume_ratio_20d,
            turnover_rate=snapshot.turnover_rate,
            return_1d=returns[1],
            return_3d=returns[3],
            return_5d=returns[5],
            return_20d=returns[20],
            max_rise=max(path_returns) if path_returns else None,
            max_drawdown=min(path_returns) if path_returns else None,
            was_limit_up=(
                return1 is not None and return1 >= limit_ratio - 0.005
            ),
            was_limit_down=(
                return1 is not None and return1 <= -limit_ratio + 0.005
            ),
            was_suspended=(
                bool(last and last.volume is not None and last.volume <= 0)
                if last is not None
                else None
            ),
            data_complete=not missing,
            evidence_ids=[
                bar.canonical_record_id
                for bar in future[:20]
            ],
            missing_fields=missing,
            generated_at=generated_at,
            algorithm_version=CAPITAL_FLOW_EVALUATION_VERSION,
        )
        return self.repository.save_evaluation(evaluation)


__all__ = ["CapitalFlowEvaluationService"]
