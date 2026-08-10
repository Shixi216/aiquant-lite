from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from database.db import get_connection
from trading.experiments.formal_replay_loader import (
    FormalReplayInputLoader,
    load_candidate_file,
)
from trading.experiments.formal_strategy_validation import FormalStrategyReplayService
from trading.experiments.parameter_sensitivity import FutureBar, REQUIRED_HORIZONS
from trading.experiments.timeline_quality_validation import (
    TimelineConstraintValidationService,
    TimelineTradeSample,
    distribution_diagnostics,
    summarize_results,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "formal_history_validation"
OUTCOME_PATH = REPORT_DIR / "formal-timeline-outcomes.json"
CANDIDATE_PATH = REPORT_DIR / "daily-candidates.json"
COVERAGE_PATH = REPORT_DIR / "daily-announcement-coverage.json"
REPORT_PATH = REPORT_DIR / "timeline-quality-validation-result.json"
TRADE_PATH = REPORT_DIR / "timeline-quality-trade-details.json"
TZ = ZoneInfo("Asia/Shanghai")
BUY_ACTIONS = {"STRONG_BUY", "BUY", "SMALL_BUY"}
TRADE_TABLES = (
    "backtest_positions", "manual_holdings", "manual_trade_ledger",
    "manual_trades", "trade_plans", "trading_orders",
)


def _trade_counts() -> dict[str, int]:
    with get_connection(read_only=True) as connection:
        return {
            table: int(connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0])
            for table in TRADE_TABLES
        }


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _load_market_data(
    symbols: list[str],
    start_date: date = date(2025, 8, 1),
    end_date: date = date(2026, 8, 6),
):
    frame = pd.DataFrame({"symbol": symbols})
    with get_connection(read_only=True) as connection:
        connection.register("_quality_symbols", frame)
        rows = connection.execute(
            """
            WITH bars AS (
                SELECT b.symbol, b.trade_date, b.open, b.high, b.low,
                       b.close, b.volume, b.amount
                FROM canonical_historical_bars b
                JOIN _quality_symbols s USING(symbol)
                WHERE b.adjustment_type = 'RAW'
                  AND b.verification_status <> 'CONFLICT'
                  AND b.trade_date BETWEEN ? AND ?
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY b.symbol, b.trade_date
                    ORDER BY b.generated_at DESC, b.bar_id
                ) = 1
            ), factors AS (
                SELECT f.symbol, f.trade_date, f.adj_factor
                FROM historical_adjustment_factors f
                JOIN _quality_symbols s USING(symbol)
                WHERE f.trade_date BETWEEN ? AND ?
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY f.symbol, f.trade_date
                    ORDER BY f.fetched_at DESC, f.factor_id
                ) = 1
            )
            SELECT b.*, f.adj_factor
            FROM bars b LEFT JOIN factors f USING(symbol, trade_date)
            ORDER BY b.symbol, b.trade_date
            """,
            [start_date, end_date, start_date, end_date],
        ).fetchall()
        benchmark_rows = connection.execute(
            """
            SELECT benchmark_code, benchmark_type, trade_date, close
            FROM historical_benchmark_bars
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY benchmark_code, trade_date
                ORDER BY fetched_at DESC, bar_id
            ) = 1
            ORDER BY benchmark_code, trade_date
            """
        ).fetchall()
        membership_rows = connection.execute(
            """
            SELECT m.symbol, m.industry_code, m.valid_from, m.valid_to,
                   m.data_available_time, m.data_cutoff
            FROM historical_industry_memberships m
            JOIN _quality_symbols s USING(symbol)
            ORDER BY m.symbol, m.valid_from DESC
            """
        ).fetchall()
        snapshot_rows = {
            "capital": connection.execute(
                """SELECT symbol, data_cutoff, score, turnover_rate
                   FROM capital_flow_symbol_snapshots ORDER BY data_cutoff"""
            ).fetchall(),
            "sentiment": connection.execute(
                """SELECT symbol, data_cutoff, score
                   FROM sentiment_symbol_snapshots ORDER BY data_cutoff"""
            ).fetchall(),
            "policy": connection.execute(
                """SELECT symbol, data_cutoff, weighted_policy_score
                   FROM policy_news_symbol_snapshots ORDER BY data_cutoff"""
            ).fetchall(),
        }
        connection.unregister("_quality_symbols")

    actual: dict[str, dict[date, FutureBar]] = defaultdict(dict)
    amounts: dict[tuple[str, date], float | None] = {}
    for symbol, day, open_, high, low, close, volume, amount, factor in rows:
        actual[symbol][day] = FutureBar(
            trade_date=day, open=float(open_), high=float(high), low=float(low),
            close=float(close), previous_close=None,
            volume=None if volume is None else float(volume),
            adjustment_factor=None if factor is None else float(factor),
        )
        amounts[(symbol, day)] = None if amount is None else float(amount)
    for symbol, values in actual.items():
        previous: float | None = None
        for day in sorted(values):
            bar = values[day]
            values[day] = FutureBar(
                trade_date=bar.trade_date, open=bar.open, high=bar.high,
                low=bar.low, close=bar.close, previous_close=previous,
                volume=bar.volume, suspended=False,
                adjustment_factor=bar.adjustment_factor,
            )
            previous = bar.close

    benchmarks: dict[str, dict[date, float]] = defaultdict(dict)
    benchmark_types: dict[str, str] = {}
    for code, kind, day, close in benchmark_rows:
        benchmarks[str(code)][day] = float(close)
        benchmark_types[str(code)] = str(kind)
    market_dates = sorted(benchmarks["000300.SH"])
    memberships: dict[str, list[tuple]] = defaultdict(list)
    for row in membership_rows:
        memberships[row[0]].append(row[1:])
    snapshots: dict[str, dict[str, list[tuple]]] = {}
    for kind, values in snapshot_rows.items():
        grouped: dict[str, list[tuple]] = defaultdict(list)
        for row in values:
            grouped[row[0]].append(row[1:])
        snapshots[kind] = grouped
    return actual, amounts, benchmarks, market_dates, memberships, snapshots


def _industry_code(memberships: dict[str, list[tuple]], symbol: str, day: date):
    cutoff = datetime.combine(day, time(16), tzinfo=TZ)
    for code, start, end, available, data_cutoff in memberships.get(symbol, []):
        if (
            start <= day and (end is None or end > day)
            and available <= cutoff and data_cutoff <= cutoff
        ):
            return str(code)
    return None


def _bars_for_sample(
    actual: dict[str, dict[date, FutureBar]],
    market_dates: list[date],
    symbol: str,
) -> tuple[FutureBar, ...]:
    output: list[FutureBar] = []
    previous: float | None = None
    values = actual[symbol]
    for day in market_dates:
        bar = values.get(day)
        if bar is None:
            output.append(FutureBar(
                trade_date=day, open=None, high=None, low=None, close=None,
                previous_close=previous, volume=None, suspended=True,
            ))
        else:
            output.append(FutureBar(
                trade_date=day, open=bar.open, high=bar.high, low=bar.low,
                close=bar.close, previous_close=previous, volume=bar.volume,
                suspended=False, adjustment_factor=bar.adjustment_factor,
            ))
            previous = bar.close
    return tuple(output)


def _latest_snapshot(rows: list[tuple], day: date):
    cutoff = datetime.combine(day, time(16), tzinfo=TZ)
    eligible = [row for row in rows if row[0] <= cutoff]
    return None if not eligible else eligible[-1]


def _ratio(current: float | None, prior: list[float], window: int):
    values = [value for value in prior[-window:] if value is not None and value > 0]
    if current is None or current <= 0 or len(values) != window:
        return None
    return current / fmean(values)


def _capital_proxy(volume_ratio: float | None, amount_ratio: float | None):
    values = []
    for value in (volume_ratio, amount_ratio):
        if value is not None and value > 0:
            values.append(max(-1.0, min(1.0, math.log(value, 2) / 2)))
    return None if not values else fmean(values)


def _feature_rows(
    preferred: list[dict[str, Any]],
    decisions: dict[tuple[str, date], Any],
    candidates: dict[tuple[str, date], Any],
    actual: dict[str, dict[date, FutureBar]],
    amounts: dict[tuple[str, date], float | None],
    snapshots: dict[str, dict[str, list[tuple]]],
) -> list[dict[str, Any]]:
    output = []
    for outcome in preferred:
        packet = outcome["packet"]
        key = (packet["symbol"], _date(packet["signal_date"]))
        decision = decisions[key]
        candidate = candidates[key]
        days = sorted(actual[key[0]])
        index = days.index(key[1])
        bar = actual[key[0]][key[1]]
        prior_days = days[:index]
        prior_volumes = [actual[key[0]][day].volume for day in prior_days]
        prior_amounts = [amounts.get((key[0], day)) for day in prior_days]
        volume_ratio = _ratio(bar.volume, prior_volumes, 20)
        amount_ratio = _ratio(amounts.get(key), prior_amounts, 20)
        capital = _latest_snapshot(snapshots["capital"].get(key[0], []), key[1])
        sentiment = _latest_snapshot(
            snapshots["sentiment"].get(key[0], []), key[1]
        )
        policy = _latest_snapshot(snapshots["policy"].get(key[0], []), key[1])
        output.append({
            "packet_id": packet["packet_id"],
            "technical_score": decision.technical_score,
            "fundamental_score": decision.fundamental_score,
            "formal_score": decision.formal_score,
            "signal_day_return": (
                None if bar.previous_close is None or bar.previous_close <= 0
                else float(bar.close) / float(bar.previous_close) - 1
            ),
            "price_distance_ma20": candidate.price / candidate.sma20 - 1,
            "ma20_ma60_distance": candidate.sma20 / candidate.sma60 - 1,
            "amount": candidate.amount,
            "turnover_rate": None if capital is None else capital[2],
            "volume_ratio_20d": volume_ratio,
            "capital_flow": (
                _capital_proxy(volume_ratio, amount_ratio)
                if capital is None else capital[1]
            ),
            "capital_flow_source": "PRICE_VOLUME_PROXY" if capital is None else "PIT_SNAPSHOT",
            "sentiment": None if sentiment is None else sentiment[1],
            "policy": None if policy is None else policy[1],
            "entry_day": outcome["preferred_entry_day"],
            "core_structure_at_entry": outcome["core_structure_at_entry"],
        })
    return output


def _group_comparison(features: list[dict[str, Any]], returns: dict[str, float]):
    fields = (
        "technical_score", "fundamental_score", "formal_score",
        "signal_day_return", "price_distance_ma20", "ma20_ma60_distance",
        "amount", "turnover_rate", "volume_ratio_20d", "capital_flow",
        "sentiment", "policy", "entry_day", "core_structure_at_entry",
    )
    groups = {
        "profit": [row for row in features if returns.get(row["packet_id"], 0) > 0],
        "loss": [row for row in features if returns.get(row["packet_id"], 0) <= 0],
    }
    result: dict[str, Any] = {"group_sizes": {k: len(v) for k, v in groups.items()}}
    effects = []
    for field in fields:
        entry: dict[str, Any] = {}
        group_values = {}
        for name, rows in groups.items():
            values = [float(row[field]) for row in rows if row.get(field) is not None]
            group_values[name] = values
            entry[name] = {
                "available": len(values),
                "mean": None if not values else fmean(values),
                "median": None if not values else median(values),
            }
        p = group_values["profit"]
        l = group_values["loss"]
        effect = None
        if len(p) >= 2 and len(l) >= 2:
            pooled = math.sqrt((pstdev(p) ** 2 + pstdev(l) ** 2) / 2)
            if pooled > 0:
                effect = (fmean(p) - fmean(l)) / pooled
        entry["standardized_difference"] = effect
        result[field] = entry
        if effect is not None and min(len(p), len(l)) >= 30:
            effects.append((abs(effect), field, effect))
    result["largest_differences"] = [
        {"field": field, "standardized_difference": effect}
        for _, field, effect in sorted(effects, reverse=True)[:5]
    ]
    return result


def _counterfactual_metrics(
    outcomes: list[dict[str, Any]],
    reason: str,
    actual: dict[str, dict[date, FutureBar]],
    market_dates: list[date],
):
    selected = [x for x in outcomes if x["terminal_reason"] == reason]
    result = {"signal_count": len(selected), "horizons": {}}
    index_by_date = {day: i for i, day in enumerate(market_dates)}
    for horizon in (5, 10, 20):
        values = []
        for item in selected:
            symbol = item["packet"]["symbol"]
            terminal = _date(item["terminal_date"])
            start_index = index_by_date.get(terminal)
            if start_index is None:
                continue
            start = actual[symbol].get(terminal)
            exit_index = start_index + horizon
            if start is None or start.close is None or exit_index >= len(market_dates):
                continue
            exit_bar = actual[symbol].get(market_dates[exit_index])
            if exit_bar is None or exit_bar.close is None:
                continue
            start_factor = start.adjustment_factor or 1.0
            exit_factor = exit_bar.adjustment_factor or 1.0
            values.append(
                float(exit_bar.close) * exit_factor
                / (float(start.close) * start_factor) - 1
            )
        result["horizons"][horizon] = {
            "sample_count": len(values),
            "win_rate": None if not values else sum(x > 0 for x in values) / len(values),
            "average_return": None if not values else fmean(values),
            "median_return": None if not values else median(values),
        }
    return result


def _market_diagnostics(
    outcomes: list[dict[str, Any]],
    constrained: dict[int, list[Any]],
):
    returns_by_horizon = {
        horizon: {
            item.packet_id: item.net_return
            for item in values if item.executed and item.net_return is not None
        }
        for horizon, values in constrained.items()
    }
    result = {}
    for regime in ("RISING", "SIDEWAYS", "FALLING"):
        packets = [x for x in outcomes if x["packet"]["market_regime"] == regime]
        buy = [x for x in packets if x["packet"]["action"] in BUY_ACTIONS]
        preferred = [x for x in buy if x["entered_preferred_zone"]]
        adverse = [float(x["maximum_adverse_excursion"]) for x in preferred
                   if x["maximum_adverse_excursion"] is not None]
        performance = {}
        for horizon in (5, 10):
            returns = returns_by_horizon[horizon]
            values = [returns[x["packet"]["packet_id"]] for x in preferred
                      if x["packet"]["packet_id"] in returns]
            performance[horizon] = {
                "sample_count": len(values),
                "win_rate": None if not values else sum(x > 0 for x in values) / len(values),
                "average_return": None if not values else fmean(values),
                "median_return": None if not values else median(values),
            }
        result[regime] = {
            "buy_signals": len(buy),
            "entered_preferred": len(preferred),
            "preferred_entry_probability": None if not buy else len(preferred) / len(buy),
            "constrained_performance": performance,
            "average_maximum_adverse_excursion": None if not adverse else fmean(adverse),
            "forbid_chase_count": sum(x["terminal_reason"] == "FORBID_CHASE" for x in buy),
            "forbid_chase_ratio": None if not buy else sum(x["terminal_reason"] == "FORBID_CHASE" for x in buy) / len(buy),
            "expired_count": sum(x["terminal_reason"] == "EXPIRED" for x in buy),
            "expired_ratio": None if not buy else sum(x["terminal_reason"] == "EXPIRED" for x in buy) / len(buy),
        }
    return result

def _drag_breakdown(before: list[Any], after: list[Any]) -> dict[str, Any]:
    scheduled = {item.packet_id: item.gross_return for item in before}
    executed = [
        item for item in after if item.executed and item.net_return is not None
        and scheduled.get(item.packet_id) is not None
    ]
    multiplier = (
        (1 - .0005) / (1 + .0005)
        * (1 - .0003 - .0005) / (1 + .0003)
    )
    scheduled_values = [float(scheduled[item.packet_id]) for item in executed]
    actual_gross = [
        (float(item.net_return) + 1) / multiplier - 1 for item in executed
    ]
    net = [float(item.net_return) for item in executed]
    return {
        "common_executed_samples": len(executed),
        "scheduled_gross_average": None if not executed else fmean(scheduled_values),
        "actual_executable_path_gross_average": None if not executed else fmean(actual_gross),
        "net_average": None if not executed else fmean(net),
        "execution_delay_effect": (
            None if not executed else fmean(scheduled_values) - fmean(actual_gross)
        ),
        "pure_commission_stamp_slippage_drag": (
            None if not executed else fmean(actual_gross) - fmean(net)
        ),
    }

def main() -> int:
    trade_before = _trade_counts()
    outcomes = json.loads(OUTCOME_PATH.read_text(encoding="utf-8"))
    preferred = [item for item in outcomes if item["entered_preferred_zone"]]
    if len(preferred) != 594:
        raise RuntimeError(f"expected 594 preferred samples, got {len(preferred)}")
    candidates_all = load_candidate_file(CANDIDATE_PATH)
    candidate_map = {(x.symbol, x.signal_date): x for x in candidates_all}
    preferred_keys = {
        (x["packet"]["symbol"], _date(x["packet"]["signal_date"]))
        for x in preferred
    }
    selected_candidates = [candidate_map[key] for key in sorted(preferred_keys)]
    coverage = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    inputs, _ = FormalReplayInputLoader().load(
        selected_candidates,
        risk_event_covered_symbols=set(coverage["successful_symbols"]),
    )
    decisions_list = FormalStrategyReplayService().replay_many(inputs)
    decisions = {(x.symbol, x.signal_date): x for x in decisions_list}
    if set(decisions) != preferred_keys:
        raise RuntimeError("preferred sample formal replay coverage mismatch")
    packet_map = {
        (x["packet"]["symbol"], _date(x["packet"]["signal_date"])): x["packet"]
        for x in preferred
    }
    mismatches = sum(
        abs(decisions[key].formal_score - float(packet_map[key]["formal_score"])) > 1e-12
        or decisions[key].action != packet_map[key]["action"]
        or tuple(decisions[key].frozen_entry_zone) != tuple(packet_map[key]["frozen_entry_zone"])
        or tuple(decisions[key].frozen_preferred_zone) != tuple(packet_map[key]["frozen_preferred_zone"])
        for key in preferred_keys
    )
    if mismatches:
        raise RuntimeError(f"frozen packet mismatch: {mismatches}")

    symbols = sorted({x["packet"]["symbol"] for x in outcomes})
    actual, amounts, benchmarks, market_dates, memberships, snapshots = (
        _load_market_data(symbols)
    )
    symbol_bars = {
        symbol: _bars_for_sample(actual, market_dates, symbol)
        for symbol in symbols
    }
    csi = benchmarks["000300.SH"]
    samples = []
    for item in preferred:
        packet = item["packet"]
        signal_date = _date(packet["signal_date"])
        industry = _industry_code(memberships, packet["symbol"], signal_date)
        samples.append(TimelineTradeSample(
            packet_id=packet["packet_id"], symbol=packet["symbol"],
            signal_date=signal_date, entry_date=_date(item["preferred_entry_date"]),
            entry_day=int(item["preferred_entry_day"]),
            market_regime=packet["market_regime"],
            core_structure_at_entry=item["core_structure_at_entry"],
            bars=symbol_bars[packet["symbol"]],
            csi300_close_by_date=csi,
            industry_close_by_date={} if industry is None else benchmarks.get(industry, {}),
        ))
    service = TimelineConstraintValidationService()
    before_results = service.evaluate_many_unconstrained(samples)
    after_results = service.evaluate_many(samples)
    comparison = {}
    distributions = {"before": {}, "after": {}}
    for horizon in REQUIRED_HORIZONS:
        before = summarize_results(before_results[horizon], constrained=False)
        after = summarize_results(after_results[horizon], constrained=True)
        comparison[horizon] = {
            "before": before,
            "after": after,
            "average_return_cost": (
                None if before["average_return"] is None or after["average_return"] is None
                else before["average_return"] - after["average_return"]
            ),
            "drag_breakdown": _drag_breakdown(
                before_results[horizon], after_results[horizon]
            ),
        }
        distributions["before"][horizon] = distribution_diagnostics(
            x.gross_return for x in before_results[horizon] if x.gross_return is not None
        )
        distributions["after"][horizon] = distribution_diagnostics(
            x.net_return for x in after_results[horizon]
            if x.executed and x.net_return is not None
        )

    features = _feature_rows(
        preferred, decisions, candidate_map, actual, amounts, snapshots
    )
    returns_5 = {
        x.packet_id: float(x.net_return) for x in after_results[5]
        if x.executed and x.net_return is not None
    }
    attribution = _group_comparison(
        [row for row in features if row["packet_id"] in returns_5], returns_5
    )
    unexecuted = {
        reason: _counterfactual_metrics(outcomes, reason, actual, market_dates)
        for reason in ("EXPIRED", "FORBID_CHASE", "SIGNAL_INVALID")
    }
    market = _market_diagnostics(outcomes, after_results)
    trade_after = _trade_counts()
    if trade_after != trade_before:
        raise RuntimeError("quality validation changed trade state")
    report = {
        "report_name": "AIQUANT-LITE timeline return quality and A-share constraints",
        "generated_at": datetime.now().astimezone(),
        "research_only": True,
        "reused_timeline_preferred_samples": len(preferred),
        "stock_selection_rerun": False,
        "formal_packet_consistency": {
            "checked": len(preferred), "mismatches": mismatches,
        },
        "constraints": {
            "t_plus_one": True,
            "buy_commission_rate": .0003,
            "sell_commission_rate": .0003,
            "stamp_duty_rate": .0005,
            "slippage_bps_each_side": 5.0,
            "limit_up_entry_block": True,
            "limit_down_exit_delay": True,
            "suspension_block": True,
            "one_price_board_block": True,
            "entry_basis": "preferred-zone entry-date close",
        },
        "constraint_comparison": comparison,
        "return_distributions": distributions,
        "profit_loss_attribution_5d_after_constraints": attribution,
        "factor_coverage": {
            "technical_fundamental_formal": len(features),
            "volume_ratio_and_price_volume_capital_proxy": sum(
                row["capital_flow"] is not None for row in features
            ),
            "turnover_rate": sum(row["turnover_rate"] is not None for row in features),
            "sentiment": sum(row["sentiment"] is not None for row in features),
            "policy": sum(row["policy"] is not None for row in features),
            "warning": "missing PIT factors are not imputed",
        },
        "market_regime_diagnostics": market,
        "unexecuted_signal_counterfactuals_from_terminal_date": unexecuted,
        "production_parameters_updated": False,
        "orders_created": 0,
        "positions_changed": 0,
        "trade_state_before": trade_before,
        "trade_state_after": trade_after,
    }
    REPORT_PATH.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, default=str
    ), encoding="utf-8")
    TRADE_PATH.write_text(json.dumps({
        "before": {
            horizon: [asdict(x) for x in values]
            for horizon, values in before_results.items()
        },
        "after": {
            horizon: [asdict(x) for x in values]
            for horizon, values in after_results.items()
        },
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
