from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from statistics import fmean, pstdev
from typing import Any

from trading.experiments.hashing import stable_id
from trading.experiments.models import (
    ExperimentRiskFlag,
    LabelStatus,
    RebalanceMethod,
    WeightingMethod,
)
from trading.experiments.schemas import (
    ExperimentObservation,
    ForwardReturnLabel,
    PortfolioBacktestRequest,
    PortfolioBacktestResult,
)


def _bounded_weights(
    scores: dict[str, float],
    *,
    method: WeightingMethod,
    cap: float,
) -> dict[str, float]:
    if not scores:
        return {}
    if method == WeightingMethod.TOP_K_EQUAL_WEIGHT:
        initial = {symbol: 1 / len(scores) for symbol in scores}
    else:
        positive = {symbol: max(0.0, value) for symbol, value in scores.items()}
        total = sum(positive.values())
        initial = (
            {symbol: 1 / len(scores) for symbol in scores}
            if total == 0
            else {symbol: value / total for symbol, value in positive.items()}
        )
    weights = {symbol: min(cap, value) for symbol, value in initial.items()}
    for _ in range(len(weights)):
        remaining = 1 - sum(weights.values())
        eligible = [
            symbol for symbol, weight in weights.items() if weight < cap
        ]
        if remaining <= 1e-12 or not eligible:
            break
        denominator = sum(initial[symbol] for symbol in eligible)
        if denominator <= 0:
            break
        for symbol in eligible:
            add = remaining * initial[symbol] / denominator
            weights[symbol] = min(cap, weights[symbol] + add)
    return weights


class PortfolioBacktestService:
    """Research-only portfolio aggregation; it never creates orders."""

    def run(
        self,
        request: PortfolioBacktestRequest,
        *,
        observations: list[ExperimentObservation],
        labels: list[ForwardReturnLabel],
    ) -> PortfolioBacktestResult:
        observation_map = {
            item.observation_id: item for item in observations
        }
        grouped: dict[
            date,
            list[tuple[ExperimentObservation, ForwardReturnLabel]],
        ] = defaultdict(list)
        for label in labels:
            observation = observation_map.get(label.observation_id)
            if (
                observation is None
                or observation.signal_type != request.signal_type
                or label.horizon_trading_days != request.horizon_trading_days
                or label.label_status != LabelStatus.COMPLETE
                or label.gross_return is None
                or not observation.point_in_time_valid
                or observation.stale_snapshot
            ):
                continue
            grouped[observation.signal_trade_date].append(
                (observation, label)
            )
        signal_dates = sorted(grouped)
        if request.rebalance_method == RebalanceMethod.SINGLE_PERIOD:
            signal_dates = signal_dates[:1]
        elif request.rebalance_method == RebalanceMethod.NON_OVERLAPPING_COHORT:
            signal_dates = signal_dates[:: request.horizon_trading_days]
        positions: list[dict[str, Any]] = []
        cohort_returns: list[float] = []
        benchmark_returns: list[float] = []
        turnovers: list[float] = []
        previous_weights: dict[str, float] = {}
        for signal_date in signal_dates:
            candidates = sorted(
                grouped[signal_date],
                key=lambda pair: (pair[0].rank, pair[0].symbol),
            )[: request.top_k]
            scores = {
                observation.symbol: (
                    observation.scanner_score
                    if observation.scanner_score is not None
                    else 0.0
                )
                for observation, _ in candidates
            }
            weights = _bounded_weights(
                scores,
                method=request.weighting_method,
                cap=request.max_position_weight,
            )
            turnover = 0.5 * sum(
                abs(weights.get(symbol, 0) - previous_weights.get(symbol, 0))
                for symbol in set(weights) | set(previous_weights)
            )
            if not previous_weights:
                turnover = sum(weights.values())
            turnovers.append(turnover)
            previous_weights = weights
            cohort_return = sum(
                weights[observation.symbol] * float(label.gross_return)
                for observation, label in candidates
            )
            cohort_returns.append(cohort_return)
            available_benchmarks = [
                label.benchmark_return
                for _, label in candidates
                if label.benchmark_return is not None
            ]
            if available_benchmarks:
                benchmark_returns.append(fmean(available_benchmarks))
            cohort_id = stable_id(
                "coh",
                {
                    "run_id": request.run_id,
                    "signal_date": signal_date,
                    "horizon": request.horizon_trading_days,
                    "signal_type": request.signal_type.value,
                },
            )
            for observation, label in candidates:
                positions.append(
                    {
                        "cohort_id": cohort_id,
                        "signal_trade_date": signal_date,
                        "symbol": observation.symbol,
                        "weight": weights[observation.symbol],
                        "entry_trade_date": label.entry_trade_date,
                        "exit_trade_date": label.exit_trade_date,
                        "gross_return": label.gross_return,
                        "net_return": None,
                        "tradable": True,
                        "risk_flags": [
                            flag.value for flag in label.risk_flags
                        ],
                    }
                )
        cumulative_path: list[float] = []
        value = 1.0
        for item in cohort_returns:
            value *= 1 + item
            cumulative_path.append(value)
        maximum_drawdown = None
        if cumulative_path:
            peak = 1.0
            drawdowns: list[float] = []
            for item in cumulative_path:
                peak = max(peak, item)
                drawdowns.append(item / peak - 1)
            maximum_drawdown = min(drawdowns)
        gross_return = None if not cohort_returns else value - 1
        benchmark_return = (
            None
            if not benchmark_returns
            else math.prod(1 + item for item in benchmark_returns) - 1
        )
        net_return = None
        flags = [
            ExperimentRiskFlag.RESEARCH_ONLY,
            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
            ExperimentRiskFlag.MULTIPLE_TESTING_RISK,
            ExperimentRiskFlag.CORPORATE_ACTION_RISK,
        ]
        if request.cost_model is None:
            flags.append(ExperimentRiskFlag.COST_MODEL_NOT_CONFIGURED)
        elif cohort_returns:
            commission = float(request.cost_model.get("commission_rate", 0))
            slippage_bps = float(request.cost_model.get("slippage_bps", 0))
            total_cost = sum(turnovers) * (
                commission * 2 + slippage_bps * 2 / 10_000
            )
            net_return = gross_return - total_cost if gross_return is not None else None
        if not cohort_returns:
            flags.extend(
                [
                    ExperimentRiskFlag.INSUFFICIENT_DATA,
                    ExperimentRiskFlag.SAMPLE_TOO_SMALL,
                ]
            )
        active_days = len(cohort_returns)
        annualized_available = active_days >= 60
        volatility = (
            pstdev(cohort_returns) if len(cohort_returns) >= 2 else None
        )
        annualized_volatility = (
            volatility * math.sqrt(252)
            if annualized_available and volatility is not None
            else None
        )
        annualized_return = (
            (1 + gross_return) ** (252 / active_days) - 1
            if annualized_available and gross_return is not None
            else None
        )
        downside = [min(0.0, item) for item in cohort_returns]
        downside_deviation = (
            math.sqrt(fmean(value**2 for value in downside))
            if downside
            else None
        )
        sharpe = (
            fmean(cohort_returns) / volatility * math.sqrt(252)
            if annualized_available and volatility
            else None
        )
        excess_series = [
            left - right
            for left, right in zip(
                cohort_returns,
                benchmark_returns,
                strict=False,
            )
        ]
        tracking_error = (
            pstdev(excess_series) if len(excess_series) >= 2 else None
        )
        information_ratio = (
            fmean(excess_series) / tracking_error * math.sqrt(252)
            if annualized_available and tracking_error
            else None
        )
        backtest_id = stable_id(
            "bt",
            request.model_dump(exclude={"persist"}, mode="json"),
        )
        quality = {
            "active_days": active_days,
            "position_count": len(positions),
            "annualization_available": annualized_available,
            "cost_model_configured": request.cost_model is not None,
        }
        return PortfolioBacktestResult(
            backtest_id=backtest_id,
            experiment_id=request.experiment_id,
            run_id=request.run_id,
            weighting_method=request.weighting_method,
            rebalance_method=request.rebalance_method,
            horizon_trading_days=request.horizon_trading_days,
            top_k=request.top_k,
            max_position_weight=request.max_position_weight,
            cumulative_return=gross_return,
            annualized_return=annualized_return,
            annualized_volatility=annualized_volatility,
            maximum_drawdown=maximum_drawdown,
            sharpe_ratio=sharpe,
            downside_deviation=downside_deviation,
            turnover=None if not turnovers else fmean(turnovers),
            gross_return=gross_return,
            net_return=net_return,
            benchmark_return=benchmark_return,
            excess_return=(
                None
                if gross_return is None or benchmark_return is None
                else gross_return - benchmark_return
            ),
            information_ratio=information_ratio,
            active_days=active_days,
            positions=positions,
            persisted=request.persist,
            point_in_time_status=(
                "VALID_PARTIAL_REPLAY"
                if cohort_returns
                else "INSUFFICIENT_DATA"
            ),
            sample_count=len(positions),
            data_quality_summary=quality,
            risk_flags=list(dict.fromkeys(flags)),
        )


__all__ = ["PortfolioBacktestService"]
