from trading.experiments.technical_overheat_shadow import (
    chronological_segments, extreme_spread, group_metrics,
    quartile_edges, quartile_label, quartile_metrics,
)


def _row(day, value, ret):
    return {
        "signal_date": day, "indicator": value,
        "return_5": ret, "return_10": ret, "return_20": ret,
        "maximum_favorable_excursion": max(ret, 0),
        "maximum_adverse_excursion": min(ret, 0),
    }


def test_chronological_segments_keep_embargo_between_periods():
    rows = [_row(f"2026-01-{day:02d}", day, 0) for day in range(1, 11)]
    result = chronological_segments(rows, research_fraction=.5, embargo_dates=2)
    assert [row["signal_date"] for row in result["research"]][-1] == "2026-01-05"
    assert [row["signal_date"] for row in result["embargo"]] == ["2026-01-06", "2026-01-07"]
    assert [row["signal_date"] for row in result["validation"]][0] == "2026-01-08"


def test_quartile_edges_are_research_only_values():
    rows = [_row(str(i), i, 0) for i in range(1, 9)]
    assert quartile_edges(rows, "indicator") == (3.0, 5.0, 6.0)


def test_quartile_label_uses_frozen_edges():
    edges = (1.0, 2.0, 3.0)
    assert quartile_label(.5, edges) == "Q1"
    assert quartile_label(1.5, edges) == "Q2"
    assert quartile_label(2.5, edges) == "Q3"
    assert quartile_label(3.5, edges) == "Q4"


def test_group_metrics_keeps_returns_and_excursions_separate():
    result = group_metrics([_row("a", 1, .1), _row("b", 2, -.2)])
    assert result["returns"]["20"]["average_return"] == -.05
    assert result["maximum_favorable_excursion"]["mean"] == .05
    assert result["maximum_adverse_excursion"]["mean"] == -.1


def test_extreme_spread_is_q4_minus_q1():
    rows = [_row(str(i), i, value) for i, value in enumerate((.1, .2, .3, .4), 1)]
    groups = quartile_metrics(rows, "indicator", (2, 3, 4))
    assert abs(extreme_spread(groups) - .3) < 1e-12
