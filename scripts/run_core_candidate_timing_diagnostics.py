"""Read-only timing diagnosis for two-year formal CORE candidates."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from scripts.run_formal_timeline_replay import _trade_counts
from scripts.run_timeline_quality_validation import (
    _date, _latest_snapshot, _load_market_data, _ratio,
)
from trading.experiments.core_timing_diagnostics import (
    bucket_performance, delay_distribution, feature_group_summary,
    outcome_groups, performance_summary,
)
from trading.experiments.formal_replay_loader import FormalReplayInputLoader, load_candidate_file
from trading.experiments.formal_strategy_validation import FormalStrategyReplayService
from trading.research.technical.analysis import technical_signal
from trading.schemas import Bar


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "formal_history_validation"
CANDIDATE_PATH = REPORT_DIR / "two-year-daily-candidates.json"
OUTCOME_PATH = REPORT_DIR / "two-year-formal-timeline-outcomes.json"
COVERAGE_PATH = REPORT_DIR / "two-year-announcement-coverage.json"
REPORT_PATH = REPORT_DIR / "core-candidate-timing-diagnostics.json"
START = date(2024, 8, 1)
END = date(2026, 8, 6)
PRIMARY_HORIZON = 20


FEATURE_FIELDS = (
    "signal_day_return", "price_distance_ma20", "price_distance_ma60",
    "ma20_ma60_distance", "prior_return_5", "prior_return_10",
    "prior_return_20", "volume_ratio_20", "turnover_rate", "amount",
    "amount_ratio_20", "technical_score", "fundamental_score",
    "formal_score", "entry_day", "maximum_favorable_excursion",
    "maximum_adverse_excursion", "actual_signal_delay",
    "coarse_filter_delay", "packet_after_first_core_scan",
    "technical_score_at_earliest_core", "technical_score_change_from_earliest_core",
)


def _return(current: float, history: list[float], periods: int) -> float | None:
    return None if len(history) < periods else current / history[-periods] - 1


def _range_bucket(value: float | None, edges: list[tuple[float, str]], tail: str):
    if value is None:
        return None
    for ceiling, label in edges:
        if value < ceiling:
            return label
    return tail


def _quantile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * proportion)]


def main() -> int:
    trade_before = _trade_counts()
    candidates = load_candidate_file(CANDIDATE_PATH)
    candidate_map = {(item.symbol, item.signal_date): item for item in candidates}
    core_scan_dates: dict[str, list[date]] = defaultdict(list)
    for item in candidates:
        if str(item.layer) == "CORE":
            core_scan_dates[item.symbol].append(item.signal_date)

    outcomes_all = json.loads(OUTCOME_PATH.read_text(encoding="utf-8"))
    outcomes = [item for item in outcomes_all
                if item["packet"]["layer"] == "CORE"
                and item["entered_preferred_zone"]
                and item["returns"].get(str(PRIMARY_HORIZON)) is not None]
    keys = {(item["packet"]["symbol"], _date(item["packet"]["signal_date"]))
            for item in outcomes}
    selected_candidates = [candidate_map[key] for key in sorted(keys)]
    coverage = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    inputs, replay_coverage = FormalReplayInputLoader().load(
        selected_candidates,
        risk_event_covered_symbols=set(coverage["successful_symbols"]),
    )
    inputs = [item for item in inputs if item.financial_records]
    decisions_list = FormalStrategyReplayService().replay_many(inputs)
    decisions = {(item.symbol, item.signal_date): item for item in decisions_list}
    if set(decisions) != keys:
        missing = sorted(keys - set(decisions))
        raise RuntimeError(f"formal decision coverage mismatch: {missing[:3]}")

    symbols = sorted({symbol for symbol, _ in keys})
    actual, amounts, _, market_dates, _, snapshots = _load_market_data(
        symbols, START, END,
    )
    market_index = {day: index for index, day in enumerate(market_dates)}
    dates_by_symbol = {symbol: sorted(actual[symbol]) for symbol in symbols}
    point_cache: dict[tuple[str, date], dict[str, float] | None] = {}

    def point(symbol: str, day: date) -> dict[str, float] | None:
        key = (symbol, day)
        if key in point_cache:
            return point_cache[key]
        dates = dates_by_symbol[symbol]
        if day not in actual[symbol]:
            point_cache[key] = None
            return None
        index = dates.index(day)
        if index < 59:
            point_cache[key] = None
            return None
        chosen = dates[index - 59:index + 1]
        bars = []
        for value_day in chosen:
            value = actual[symbol][value_day]
            bars.append(Bar(
                trade_date=value_day, open=float(value.open), high=float(value.high),
                low=float(value.low), close=float(value.close),
                volume=0.0 if value.volume is None else float(value.volume),
            ))
        closes = [bar.close for bar in bars]
        result = {
            "price": closes[-1], "sma20": fmean(closes[-20:]),
            "sma60": fmean(closes), "technical_score": technical_signal(bars).score,
        }
        point_cache[key] = result
        return result

    rows = []
    for outcome in outcomes:
        packet = outcome["packet"]
        symbol = packet["symbol"]
        signal_date = _date(packet["signal_date"])
        key = (symbol, signal_date)
        candidate = candidate_map[key]
        decision = decisions[key]
        dates = dates_by_symbol[symbol]
        signal_symbol_index = dates.index(signal_date)
        signal_market_index = market_index[signal_date]
        signal_bar = actual[symbol][signal_date]
        previous_dates = dates[:signal_symbol_index]
        previous_closes = [float(actual[symbol][day].close) for day in previous_dates]
        previous_volumes = [actual[symbol][day].volume for day in previous_dates]
        previous_amounts = [amounts.get((symbol, day)) for day in previous_dates]
        lookback_market_dates = market_dates[max(0, signal_market_index - 10):signal_market_index + 1]
        points = [(day, point(symbol, day)) for day in lookback_market_dates]
        structure_dates = [day for day, value in points if value is not None
                           and value["price"] > value["sma20"] > value["sma60"]]
        score_dates = [day for day, value in points if value is not None
                       and value["technical_score"] > .3]
        core_dates = [day for day, value in points if value is not None
                      and value["price"] > value["sma20"] > value["sma60"]
                      and value["technical_score"] > .3]
        if not core_dates:
            raise RuntimeError(f"CORE condition absent at signal: {key}")
        earliest_core = core_dates[0]
        first_scanned = next(
            (day for day in sorted(core_scan_dates[symbol])
             if earliest_core <= day <= signal_date), signal_date,
        )
        earliest_point = point(symbol, earliest_core)
        capital = _latest_snapshot(snapshots["capital"].get(symbol, []), signal_date)
        current_amount = amounts.get(key)
        row = {
            "packet_id": packet["packet_id"], "symbol": symbol,
            "signal_date": str(signal_date),
            "earliest_structure_date": None if not structure_dates else str(structure_dates[0]),
            "earliest_score_date": None if not score_dates else str(score_dates[0]),
            "earliest_core_condition_date": str(earliest_core),
            "first_core_scan_date": str(first_scanned),
            "actual_packet_signal_date": str(signal_date),
            "signal_day_return": (
                None if signal_bar.previous_close is None
                else float(signal_bar.close) / float(signal_bar.previous_close) - 1
            ),
            "price_distance_ma20": candidate.price / candidate.sma20 - 1,
            "price_distance_ma60": candidate.price / candidate.sma60 - 1,
            "ma20_ma60_distance": candidate.sma20 / candidate.sma60 - 1,
            "prior_return_5": _return(candidate.price, previous_closes, 5),
            "prior_return_10": _return(candidate.price, previous_closes, 10),
            "prior_return_20": _return(candidate.price, previous_closes, 20),
            "volume_ratio_20": _ratio(signal_bar.volume, previous_volumes, 20),
            "turnover_rate": None if capital is None else capital[2],
            "amount": candidate.amount,
            "amount_ratio_20": _ratio(current_amount, previous_amounts, 20),
            "technical_score": decision.technical_score,
            "fundamental_score": decision.fundamental_score,
            "formal_score": decision.formal_score,
            "entry_day": outcome["preferred_entry_day"],
            "maximum_favorable_excursion": outcome["maximum_favorable_excursion"],
            "maximum_adverse_excursion": outcome["maximum_adverse_excursion"],
            "return_5": outcome["returns"].get("5"),
            "return_10": outcome["returns"].get("10"),
            "return_20": outcome["returns"].get("20"),
            "actual_signal_delay": signal_market_index - market_index[earliest_core],
            "coarse_filter_delay": market_index[first_scanned] - market_index[earliest_core],
            "packet_after_first_core_scan": signal_market_index - market_index[first_scanned],
            "technical_score_at_earliest_core": earliest_point["technical_score"],
            "technical_score_change_from_earliest_core": (
                decision.technical_score - earliest_point["technical_score"]
            ),
            "lookback_left_censored": (
                earliest_core == lookback_market_dates[0]
            ),
        }
        rows.append(row)

    groups = outcome_groups(rows)
    group_features = feature_group_summary(groups, FEATURE_FIELDS)
    group_performance = {name: performance_summary(values) for name, values in groups.items()}
    ma_values = [float(row["ma20_ma60_distance"]) for row in rows]
    q25, q50, q75 = (_quantile(ma_values, value) for value in (.25, .50, .75))
    buckets = {
        "signal_day_return": bucket_performance(rows, lambda row: _range_bucket(
            row["signal_day_return"], [(0, "<0%"), (.03, "0-3%"),
            (.05, "3-5%"), (.07, "5-7%")], ">=7%")),
        "price_distance_ma20": bucket_performance(rows, lambda row: _range_bucket(
            row["price_distance_ma20"], [(0, "<0%"), (.05, "0-5%"),
            (.10, "5-10%"), (.15, "10-15%"), (.20, "15-20%")], ">=20%")),
        "prior_return_10": bucket_performance(rows, lambda row: _range_bucket(
            row["prior_return_10"], [(.05, "<5%"), (.10, "5-10%"),
            (.20, "10-20%")], ">=20%")),
        "ma20_ma60_quartile": bucket_performance(rows, lambda row: _range_bucket(
            row["ma20_ma60_distance"], [(q25, "Q1"), (q50, "Q2"),
            (q75, "Q3")], "Q4")),
        "actual_signal_delay": bucket_performance(rows, lambda row: (
            str(row["actual_signal_delay"]) if row["actual_signal_delay"] < 5 else "5_plus"
        )),
        "coarse_filter_delay": bucket_performance(rows, lambda row: (
            str(row["coarse_filter_delay"]) if row["coarse_filter_delay"] < 5 else "5_plus"
        )),
    }
    top_examples = sorted(groups["top_10pct"], key=lambda row: row["return_20"], reverse=True)
    report = {
        "report_name": "AIQUANT-LITE CORE candidate timing diagnostics",
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_only": True,
        "primary_grouping_horizon": 20,
        "coverage": {
            "core_formal_packets": sum(
                item["packet"]["layer"] == "CORE" for item in outcomes_all
            ),
            "core_preferred_with_20d_return": len(rows),
            "symbols": len({row["symbol"] for row in rows}),
            "start": min(row["signal_date"] for row in rows),
            "end": max(row["signal_date"] for row in rows),
            "formal_replay": asdict(replay_coverage),
        },
        "group_features": group_features,
        "group_performance": group_performance,
        "position_buckets": buckets,
        "ma20_ma60_quartile_edges": [q25, q50, q75],
        "timing": {
            "actual_packet_minus_earliest_core": delay_distribution(rows, "actual_signal_delay"),
            "first_core_scan_minus_earliest_core": delay_distribution(rows, "coarse_filter_delay"),
            "packet_minus_first_core_scan": delay_distribution(rows, "packet_after_first_core_scan"),
            "left_censored_at_10_days": sum(row["lookback_left_censored"] for row in rows),
            "performance_by_delay": buckets["actual_signal_delay"],
        },
        "sample_rows": rows,
        "top_10pct_winner_examples": top_examples[:25],
        "data_definition": {
            "returns": "after frozen preferred-zone entry; no parameter refit",
            "earliest_condition_lookback": "maximum 10 market trading dates",
            "coarse_filter": "positive day, price 10-200, amount >= 300m, daily amount rank <= 30",
            "formal_scores": "replayed production 60/40 DecisionEngine chain",
            "point_in_time_cutoff": "signal date 16:00",
        },
        "production_parameters_modified": False,
        "orders_created": 0,
        "positions_changed": 0,
        "trade_state_before": trade_before,
        "trade_state_after": _trade_counts(),
    }
    if report["trade_state_after"] != trade_before:
        raise RuntimeError("diagnostics changed trade state")
    REPORT_PATH.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    print(json.dumps({
        "coverage": report["coverage"], "timing": report["timing"],
        "top_effects": group_features["profit_loss_effect_ranking"][:8],
        "trade_state_unchanged": True,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
