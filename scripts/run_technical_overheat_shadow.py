"""Read-only shadow diagnosis separating trend slope from overheat."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any

from scripts.run_formal_timeline_replay import _trade_counts
from scripts.run_timeline_quality_validation import _load_market_data
from trading.experiments.technical_overheat_shadow import (
    chronological_segments, extreme_spread, group_metrics,
    quartile_edges, quartile_metrics, quantile,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "formal_history_validation"
SOURCE_PATH = REPORT_DIR / "core-candidate-timing-diagnostics.json"
REPORT_PATH = REPORT_DIR / "technical-overheat-shadow-diagnostics.json"
START = date(2024, 8, 1)
END = date(2026, 8, 6)
INDICATORS = (
    "ma20_slope_5", "ma60_slope_5", "ma20_ma60_expansion",
    "price_distance_ma20", "price_distance_ma60", "prior_return_5",
    "prior_return_10", "prior_return_20", "expansion_speed_5",
)


def _moving_averages(actual, symbol: str, day: date) -> tuple[float, float] | None:
    dates = sorted(value for value in actual[symbol] if value <= day)
    if len(dates) < 60:
        return None
    closes = [float(actual[symbol][value].close) for value in dates[-60:]]
    return sum(closes[-20:]) / 20, sum(closes) / 60


def _tail_groups(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: float(row["return_20"]))
    count = max(1, round(len(ordered) * .10)) if ordered else 0
    return {
        "profit": group_metrics(row for row in rows if float(row["return_20"]) > 0),
        "loss": group_metrics(row for row in rows if float(row["return_20"]) <= 0),
        "top_10pct": group_metrics(ordered[-count:] if count else []),
        "bottom_10pct": group_metrics(ordered[:count] if count else []),
    }


def main() -> int:
    trade_before = _trade_counts()
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    rows = source.get("sample_rows", [])
    if len(rows) != source["coverage"]["core_preferred_with_20d_return"]:
        raise RuntimeError("core timing sample details are incomplete")
    symbols = sorted({row["symbol"] for row in rows})
    actual, _, _, market_dates, _, _ = _load_market_data(symbols, START, END)
    market_index = {day: index for index, day in enumerate(market_dates)}
    enriched = []
    for raw in rows:
        row = dict(raw)
        signal_date = date.fromisoformat(row["signal_date"])
        index = market_index[signal_date]
        if index < 5:
            continue
        previous_date = market_dates[index - 5]
        current = _moving_averages(actual, row["symbol"], signal_date)
        previous = _moving_averages(actual, row["symbol"], previous_date)
        if current is None or previous is None:
            continue
        ma20, ma60 = current
        old_ma20, old_ma60 = previous
        row.update({
            "ma20_slope_5": ma20 / old_ma20 - 1,
            "ma60_slope_5": ma60 / old_ma60 - 1,
            "ma20_ma60_expansion": ma20 / ma60 - 1,
            "expansion_speed_5": (ma20 / ma60 - 1) - (old_ma20 / old_ma60 - 1),
        })
        enriched.append(row)

    segments = chronological_segments(enriched, research_fraction=.60, embargo_dates=20)
    research = segments["research"]
    validation = segments["validation"]
    if min(len(research), len(validation)) < 100:
        raise RuntimeError("independent time segment sample is too small")
    edges = {field: quartile_edges(research, field) for field in INDICATORS}
    indicator_results = {}
    research_ranking = []
    for field in INDICATORS:
        research_groups = quartile_metrics(research, field, edges[field])
        validation_groups = quartile_metrics(validation, field, edges[field])
        research_spread = extreme_spread(research_groups)
        validation_spread = extreme_spread(validation_groups)
        consistent = (
            research_spread is not None and validation_spread is not None
            and research_spread * validation_spread > 0
        )
        indicator_results[field] = {
            "research_frozen_quartile_edges": edges[field],
            "research": research_groups,
            "validation": validation_groups,
            "q4_minus_q1_20d": {
                "research": research_spread,
                "validation": validation_spread,
                "same_direction": consistent,
            },
        }
        if research_spread is not None:
            research_ranking.append((abs(research_spread), field))
    research_ranking.sort(reverse=True)
    research_selected = research_ranking[0][1]

    ma20_high = median(float(row["ma20_slope_5"]) for row in research)
    expansion_q25, _, expansion_q75 = edges["ma20_ma60_expansion"]
    technical_high = quantile(
        (float(row["technical_score"]) for row in research), .75,
    )

    slope_expansion = {}
    technical_high_groups = {}
    for name, values in (("research", research), ("validation", validation)):
        high_slope_moderate = [
            row for row in values
            if row["ma20_slope_5"] >= ma20_high
            and expansion_q25 <= row["ma20_ma60_expansion"] < expansion_q75
        ]
        high_slope_overheated = [
            row for row in values
            if row["ma20_slope_5"] >= ma20_high
            and row["ma20_ma60_expansion"] >= expansion_q75
        ]
        slope_expansion[name] = {
            "high_slope_moderate_expansion": group_metrics(high_slope_moderate),
            "high_slope_large_expansion": group_metrics(high_slope_overheated),
        }
        technical_high_groups[name] = _tail_groups([
            row for row in values if row["technical_score"] >= technical_high
        ])

    trade_after = _trade_counts()
    if trade_after != trade_before:
        raise RuntimeError("shadow diagnostics changed trade state")
    report = {
        "report_name": "AIQUANT-LITE technical overheat shadow diagnostics",
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_only": True,
        "shadow_metrics_connected_to_production": False,
        "definitions": {
            "ma20_slope_5": "MA20(t) / MA20(t-5 market days) - 1",
            "ma60_slope_5": "MA60(t) / MA60(t-5 market days) - 1",
            "ma20_ma60_expansion": "MA20 / MA60 - 1",
            "expansion_speed_5": "current expansion minus expansion five market days ago",
            "quartile_edges": "frozen from research segment only",
        },
        "coverage": {
            "total_samples": len(enriched),
            "research_samples": len(research),
            "embargo_samples": len(segments["embargo"]),
            "validation_samples": len(validation),
            "research_dates": [research[0]["signal_date"], research[-1]["signal_date"]],
            "validation_dates": [validation[0]["signal_date"], validation[-1]["signal_date"]],
        },
        "frozen_thresholds_from_research": {
            "high_ma20_slope_median": ma20_high,
            "moderate_expansion_lower_q25": expansion_q25,
            "large_expansion_q75": expansion_q75,
            "high_technical_score_q75": technical_high,
        },
        "high_slope_comparison": slope_expansion,
        "high_technical_score_outcomes": technical_high_groups,
        "indicator_quartiles": indicator_results,
        "research_selected_indicator": {
            "field": research_selected,
            "selection_rule": "largest absolute research Q4-minus-Q1 20d spread",
            "independent_validation": indicator_results[research_selected]["q4_minus_q1_20d"],
        },
        "production_parameters_modified": False,
        "orders_created": 0,
        "positions_changed": 0,
        "trade_state_before": trade_before,
        "trade_state_after": trade_after,
    }
    REPORT_PATH.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    print(json.dumps({
        "coverage": report["coverage"],
        "research_selected_indicator": report["research_selected_indicator"],
        "high_slope_comparison": slope_expansion,
        "trade_state_unchanged": True,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
