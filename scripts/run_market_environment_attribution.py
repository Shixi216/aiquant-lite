from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
from statistics import fmean, median
from typing import Any
from zoneinfo import ZoneInfo

from database.db import get_connection
from scripts.run_timeline_quality_validation import (
    COVERAGE_PATH, CANDIDATE_PATH, OUTCOME_PATH, TRADE_PATH,
    _date, _load_market_data, _trade_counts,
)
from trading.experiments.formal_replay_loader import FormalReplayInputLoader, load_candidate_file
from trading.experiments.formal_strategy_validation import FormalStrategyReplayService
from trading.experiments.market_environment_validation import MarketState, chronological_split, condition_flags
from trading.experiments.portfolio_curve_validation import PortfolioTrade, simulate_portfolio_curve


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "reports" / "formal_history_validation" / "market-environment-attribution.json"
TZ = ZoneInfo("Asia/Shanghai")
HORIZONS = (5, 10, 20)


def _market_states(
    start_date: date = date(2025, 8, 1),
    end_date: date = date(2026, 8, 6),
) -> dict[date, MarketState]:
    with get_connection(read_only=True) as connection:
        csi_rows = connection.execute("""
            WITH x AS (
              SELECT trade_date, close,
                     AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) ma20,
                     AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) ma60,
                     LAG(close, 20) OVER (ORDER BY trade_date) close20,
                     COUNT(*) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) n60
              FROM historical_benchmark_bars
              WHERE benchmark_code='000300.SH'
                AND data_available_time <= trade_date + INTERVAL 16 HOUR
                AND data_cutoff <= trade_date + INTERVAL 16 HOUR
              QUALIFY ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY fetched_at DESC, bar_id)=1
            )
            SELECT trade_date, close,
                   CASE WHEN n60 >= 20 THEN ma20 END,
                   CASE WHEN n60 >= 60 THEN ma60 END,
                   CASE WHEN close20 > 0 THEN close/close20-1 END
            FROM x ORDER BY trade_date
        """).fetchall()
        breadth_rows = connection.execute("""
            WITH raw AS (
              SELECT symbol, trade_date, close, amount
              FROM canonical_historical_bars
              WHERE adjustment_type='RAW' AND verification_status <> 'CONFLICT'
                AND trade_date BETWEEN ? AND ?
                AND data_available_time <= trade_date + INTERVAL 16 HOUR
                AND data_cutoff <= trade_date + INTERVAL 16 HOUR
              QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, trade_date ORDER BY generated_at DESC, bar_id
              )=1
            ), feature AS (
              SELECT symbol, trade_date, close, amount,
                     LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) previous_close,
                     AVG(close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) ma20,
                     COUNT(*) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) n20
              FROM raw
            ), daily AS (
              SELECT trade_date,
                     SUM(CASE WHEN close > previous_close THEN 1 ELSE 0 END) advancers,
                     SUM(CASE WHEN close < previous_close THEN 1 ELSE 0 END) decliners,
                     SUM(CASE WHEN n20=20 AND close > ma20 THEN 1 ELSE 0 END)::DOUBLE /
                       NULLIF(SUM(CASE WHEN n20=20 THEN 1 ELSE 0 END), 0) above_ma20_ratio,
                     SUM(amount) amount
              FROM feature GROUP BY trade_date
            ), final AS (
              SELECT *, AVG(amount) OVER (
                ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
              ) amount_avg20,
              COUNT(amount) OVER (
                ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
              ) amount_n20
              FROM daily
            )
            SELECT trade_date, advancers, decliners, above_ma20_ratio,
                   CASE WHEN amount_n20=20 AND amount_avg20>0 THEN amount/amount_avg20 END
            FROM final ORDER BY trade_date
        """, [start_date, end_date]).fetchall()
    breadth = {row[0]: row[1:] for row in breadth_rows}
    states = {}
    for day, close, ma20, ma60, return20 in csi_rows:
        if day not in breadth:
            continue
        adv, dec, above, amount_ratio = breadth[day]
        states[day] = MarketState(
            trade_date=day, csi300_close=float(close),
            csi300_ma20=None if ma20 is None else float(ma20),
            csi300_ma60=None if ma60 is None else float(ma60),
            csi300_return20=None if return20 is None else float(return20),
            advancers=int(adv), decliners=int(dec),
            above_ma20_ratio=None if above is None else float(above),
            amount_ratio20=None if amount_ratio is None else float(amount_ratio),
        )
    return states


def _signal_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["net_return"]) for row in rows if row["executed"] and row["net_return"] is not None]
    gains = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    return {
        "sample_count": len(values),
        "win_rate": None if not values else sum(x > 0 for x in values) / len(values),
        "average_return": None if not values else fmean(values),
        "median_return": None if not values else median(values),
        "profit_loss_ratio": None if not gains or not losses else fmean(gains) / abs(fmean(losses)),
    }


def _curve(rows: list[dict[str, Any]], weights: dict[str, float], actual, market_dates) -> dict[str, Any]:
    trades = []
    for row in rows:
        if not row["executed"] or row["net_return"] is None or row["exit_date"] is None:
            continue
        entry, exit_ = _date(row["entry_date"]), _date(row["exit_date"])
        bars = actual[row["symbol"]]
        marks = {
            day: float(bar.close) * (1.0 if bar.adjustment_factor is None else float(bar.adjustment_factor))
            for day, bar in bars.items() if entry <= day <= exit_ and bar.close is not None
        }
        if entry not in marks or exit_ not in marks:
            continue
        trades.append(PortfolioTrade(
            packet_id=row["packet_id"], symbol=row["symbol"], entry_date=entry,
            exit_date=exit_, requested_weight=weights[row["packet_id"]],
            adjusted_close_by_date=marks,
        ))
    if not trades:
        return {"maximum_drawdown": None, "filled_trades": 0}
    start, end = min(x.entry_date for x in trades), max(x.exit_date for x in trades)
    result = simulate_portfolio_curve(trades, [x for x in market_dates if start <= x <= end])
    return {key: value for key, value in asdict(result).items() if key != "daily_nav"}


def _group(rows_by_horizon, packet_ids: set[str], weights, actual, market_dates):
    result = {}
    for horizon in HORIZONS:
        rows = [row for row in rows_by_horizon[str(horizon)] if row["packet_id"] in packet_ids]
        result[str(horizon)] = {**_signal_metrics(rows), "portfolio": _curve(rows, weights, actual, market_dates)}
    return result


def main() -> int:
    trade_before = _trade_counts()
    outcomes = json.loads(OUTCOME_PATH.read_text(encoding="utf-8"))
    preferred = [x for x in outcomes if x["entered_preferred_zone"]]
    details = json.loads(TRADE_PATH.read_text(encoding="utf-8"))["after"]
    signal_date_by_id = {x["packet"]["packet_id"]: _date(x["packet"]["signal_date"]) for x in preferred}
    regime_by_id = {x["packet"]["packet_id"]: x["packet"]["market_regime"] for x in preferred}
    signal_dates = sorted({_date(x["packet"]["signal_date"]) for x in outcomes})
    research_dates, embargo_dates, validation_dates = chronological_split(signal_dates, embargo_dates=20)

    candidate_map = {(x.symbol, x.signal_date): x for x in load_candidate_file(CANDIDATE_PATH)}
    keys = {(x["packet"]["symbol"], _date(x["packet"]["signal_date"])) for x in preferred}
    coverage = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    inputs, _ = FormalReplayInputLoader().load(
        [candidate_map[key] for key in sorted(keys)],
        risk_event_covered_symbols=set(coverage["successful_symbols"]),
    )
    decisions = FormalStrategyReplayService().replay_many(inputs)
    decision_by_key = {(x.symbol, x.signal_date): x for x in decisions}
    weights = {}
    packet_mismatches = 0
    for item in preferred:
        packet = item["packet"]
        key = (packet["symbol"], _date(packet["signal_date"]))
        decision = decision_by_key[key]
        packet_mismatches += int(decision.action != packet["action"] or abs(decision.formal_score-float(packet["formal_score"])) > 1e-12)
        weights[packet["packet_id"]] = decision.target_position_ratio / decision.recommended_batches
    if packet_mismatches:
        raise RuntimeError(f"formal packet mismatches: {packet_mismatches}")

    symbols = sorted({x["packet"]["symbol"] for x in preferred})
    actual, _, _, market_dates, _, _ = _load_market_data(symbols)
    states = _market_states()
    missing_states = [day for day in signal_dates if day not in states]
    if missing_states:
        raise RuntimeError(f"missing PIT market states: {missing_states[:3]}")
    flags_by_date = {day: condition_flags(states[day]) for day in signal_dates}

    all_ids = set(signal_date_by_id)
    old_dd = {
        h: json.loads((ROOT / "reports/formal_history_validation/timeline-quality-validation-result.json").read_text(encoding="utf-8"))["constraint_comparison"][h]["after"]["portfolio_maximum_drawdown"]
        for h in map(str, HORIZONS)
    }
    baseline = _group(details, all_ids, weights, actual, market_dates)

    research_set, validation_set = set(research_dates), set(validation_dates)
    condition_results = {}
    ranking = []
    for name in condition_flags(states[signal_dates[-1]]):
        condition_results[name] = {}
        for segment, dates in (("research", research_set), ("validation", validation_set)):
            true_ids = {pid for pid, day in signal_date_by_id.items() if day in dates and flags_by_date[day][name] is True}
            false_ids = {pid for pid, day in signal_date_by_id.items() if day in dates and flags_by_date[day][name] is False}
            condition_results[name][segment] = {
                "true": _group(details, true_ids, weights, actual, market_dates),
                "false": _group(details, false_ids, weights, actual, market_dates),
            }
        score_parts = []
        enough = True
        for horizon in HORIZONS:
            t = condition_results[name]["research"]["true"][str(horizon)]
            f = condition_results[name]["research"]["false"][str(horizon)]
            if min(t["sample_count"], f["sample_count"]) < 30:
                enough = False
            elif t["average_return"] is not None and f["average_return"] is not None:
                score_parts.append(t["average_return"] - f["average_return"])
        score = None if not enough or not score_parts else fmean(score_parts)
        ranking.append({"condition": name, "research_average_return_spread": score, "minimum_30_each_side": enough})
    ranking.sort(key=lambda x: -999 if x["research_average_return_spread"] is None else x["research_average_return_spread"], reverse=True)
    selected = [x["condition"] for x in ranking if x["research_average_return_spread"] is not None and x["research_average_return_spread"] > 0][:2]

    regime_results = {}
    for regime in ("RISING", "SIDEWAYS", "FALLING"):
        ids = {pid for pid, day in signal_date_by_id.items() if day in validation_set and regime_by_id[pid] == regime}
        regime_results[regime] = _group(details, ids, weights, actual, market_dates)

    filter_results = {}
    if selected:
        normal = {pid for pid, day in signal_date_by_id.items() if day in validation_set and all(flags_by_date[day][name] is True for name in selected)}
        pause = {pid for pid, day in signal_date_by_id.items() if day in validation_set and all(flags_by_date[day][name] is False for name in selected)}
        degrade = {pid for pid, day in signal_date_by_id.items() if day in validation_set} - normal - pause
        filter_results = {
            "normal": _group(details, normal, weights, actual, market_dates),
            "degrade": _group(details, degrade, weights, actual, market_dates),
            "pause": _group(details, pause, weights, actual, market_dates),
        }

    report = {
        "generated_at": datetime.now(TZ).isoformat(), "research_only": True,
        "production_parameters_modified": False,
        "portfolio_drawdown_audit": {
            "old_metric_is_true_portfolio_curve": False,
            "old_approximation": old_dd,
            "corrected_daily_nav": baseline,
            "weight_basis": "DecisionEngine PositionSizer target_position_ratio / recommended_batches",
            "cash_cap": 1.0, "overlapping_signals_modeled": True, "t_plus_one_modeled": True,
            "actual_exit_dates_modeled": True,
        },
        "point_in_time_market_state": {
            "signal_dates": len(signal_dates), "missing_dates": len(missing_states),
            "uses_only_data_available_by_signal_date_1600": True,
        },
        "time_split": {
            "research": [str(research_dates[0]), str(research_dates[-1]), len(research_dates)],
            "embargo": [str(embargo_dates[0]), str(embargo_dates[-1]), len(embargo_dates)],
            "validation": [str(validation_dates[0]), str(validation_dates[-1]), len(validation_dates)],
        },
        "segment_baselines": {
            "research": _group(
                details,
                {pid for pid, day in signal_date_by_id.items() if day in research_set},
                weights, actual, market_dates,
            ),
            "validation": _group(
                details,
                {pid for pid, day in signal_date_by_id.items() if day in validation_set},
                weights, actual, market_dates,
            ),
        },
        "condition_ranking_research_only": ranking,
        "condition_results": condition_results,
        "selected_simple_conditions": selected,
        "independent_validation_filter_results": filter_results,
        "validation_market_regimes": regime_results,
        "formal_packet_mismatches": packet_mismatches,
        "trade_table_counts_before": trade_before,
        "trade_table_counts_after": _trade_counts(),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if report["trade_table_counts_before"] != report["trade_table_counts_after"]:
        raise RuntimeError("trade tables changed")
    print(json.dumps({"report": str(REPORT_PATH), "selected": selected, "baseline": baseline, "validation": filter_results}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
