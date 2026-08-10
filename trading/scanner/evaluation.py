from __future__ import annotations

from trading.scanner.repository import ScannerRepository
from trading.scanner.schemas import (
    ScannerEvaluationItem,
    ScannerEvaluationRequest,
    ScannerEvaluationResponse,
)


class ScannerEvaluationService:
    """Append-only post-event evaluation; it never feeds candidate ranking."""

    def __init__(self, repository: ScannerRepository) -> None:
        self.repository = repository

    def evaluate(
        self,
        request: ScannerEvaluationRequest,
    ) -> ScannerEvaluationResponse:
        context = self.repository.evaluation_context(request.run_id)
        if context is None:
            raise KeyError(f"scanner run not found: {request.run_id}")
        analysis_time, candidates = context
        if request.evaluated_at <= analysis_time:
            raise ValueError("evaluation must occur after the scanner analysis")
        future = self.repository.future_closes(
            symbols=[item.symbol for item in candidates],
            analysis_time=analysis_time,
            evaluated_at=request.evaluated_at,
        )
        items: list[ScannerEvaluationItem] = []
        for candidate in candidates:
            values = [close for _, close in future.get(candidate.symbol, [])]
            base = candidate.current_price

            def horizon(days: int) -> float | None:
                if base is None or base <= 0 or len(values) < days:
                    return None
                return values[days - 1] / base - 1

            returns = {
                "return_1d": horizon(1),
                "return_3d": horizon(3),
                "return_5d": horizon(5),
                "return_20d": horizon(20),
            }
            complete = returns["return_20d"] is not None
            maximum_upside = (
                None
                if base is None or base <= 0 or not values
                else max(value / base - 1 for value in values[:20])
            )
            maximum_drawdown = (
                None
                if base is None or base <= 0 or not values
                else min(value / base - 1 for value in values[:20])
            )
            items.append(
                ScannerEvaluationItem(
                    run_id=request.run_id,
                    symbol=candidate.symbol,
                    rank=candidate.rank,
                    scanner_score=candidate.scanner_score,
                    **returns,
                    maximum_upside=maximum_upside,
                    maximum_drawdown=maximum_drawdown,
                    is_suspended=None,
                    price_limit_up=None,
                    price_limit_down=None,
                    data_complete=complete,
                    status=(
                        "COMPLETE" if complete else "INSUFFICIENT_DATA"
                    ),
                )
            )
        persisted_count = (
            self.repository.save_evaluations(
                analysis_time=analysis_time,
                evaluated_at=request.evaluated_at,
                candidates=candidates,
                items=items,
            )
            if request.persist
            else 0
        )
        return ScannerEvaluationResponse(
            run_id=request.run_id,
            items=items,
            persisted_count=persisted_count,
        )


__all__ = ["ScannerEvaluationService"]
