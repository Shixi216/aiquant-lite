from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading.experiments.core_ranking_walk_forward import (
    assign_ranking_scores,
    build_walk_forward_folds,
    factor_information_coefficients,
    select_daily_top_fraction,
    spearman,
)


def _rows() -> list[dict]:
    day = date(2026, 1, 5)
    return [
        {
            "packet_id": "p1", "signal_date": day,
            "technical_score": 0.9, "b_score": 0.8,
            "fundamental_score": 0.1,
        },
        {
            "packet_id": "p2", "signal_date": day,
            "technical_score": 0.8, "b_score": 0.7,
            "fundamental_score": 0.9,
        },
        {
            "packet_id": "p3", "signal_date": day,
            "technical_score": 0.7, "b_score": 0.6,
            "fundamental_score": 0.2,
        },
    ]


def test_c_is_equal_rank_blend_without_optimized_weight() -> None:
    rows = assign_ranking_scores(_rows())
    values = {row["packet_id"]: row for row in rows}
    assert values["p1"]["rank_score_A"] == 0.9
    assert values["p1"]["rank_score_B"] == 0.8
    assert values["p2"]["rank_score_C"] == pytest.approx(0.75)
    assert values["p1"]["rank_score_C"] == pytest.approx(0.5)


def test_top_scopes_are_nested_and_cross_sectional_by_day() -> None:
    day = date(2026, 1, 5)
    rows = []
    for index in range(20):
        rows.append({
            "packet_id": f"p{index:02d}", "signal_date": day,
            "technical_score": index / 20,
            "b_score": index / 20,
            "fundamental_score": index / 20,
        })
    ranked = assign_ranking_scores(rows)
    top10 = select_daily_top_fraction(
        ranked, ranking="C", fraction=0.10, allowed_dates={day}
    )
    top20 = select_daily_top_fraction(
        ranked, ranking="C", fraction=0.20, allowed_dates={day}
    )
    top30 = select_daily_top_fraction(
        ranked, ranking="C", fraction=0.30, allowed_dates={day}
    )
    assert len(top10) == 2
    assert top10 < top20 < top30


def test_walk_forward_has_multiple_disjoint_validation_folds_and_embargo() -> None:
    start = date(2024, 1, 1)
    dates = [start + timedelta(days=index) for index in range(300)]
    folds = build_walk_forward_folds(dates)
    assert len(folds) >= 3
    validation = [set(fold.validation_dates) for fold in folds]
    assert all(len(fold.embargo_dates) == 20 for fold in folds)
    assert all(not left & right for left, right in zip(validation, validation[1:]))
    assert all(
        max(fold.train_dates) < min(fold.embargo_dates) < min(fold.validation_dates)
        for fold in folds
    )


def test_spearman_and_factor_diagnostics_do_not_use_missing_rows() -> None:
    assert spearman([(1, 10), (2, 20), (3, 30)]) == pytest.approx(1.0)
    rows = [
        {"packet_id": "a", "factor": 1.0},
        {"packet_id": "b", "factor": None},
        {"packet_id": "c", "factor": 3.0},
        {"packet_id": "d", "factor": 4.0},
    ]
    output = factor_information_coefficients(
        rows, {"a": 0.1, "b": 0.2, "c": 0.3, "d": 0.4}, ["factor"]
    )
    assert output["factor"]["sample_count"] == 3
    assert output["factor"]["spearman_ic"] == pytest.approx(1.0)


def test_invalid_ranking_and_fraction_are_rejected() -> None:
    rows = assign_ranking_scores(_rows())
    with pytest.raises(ValueError):
        select_daily_top_fraction(
            rows, ranking="D", fraction=0.1,
            allowed_dates={date(2026, 1, 5)},
        )
    with pytest.raises(ValueError):
        select_daily_top_fraction(
            rows, ranking="A", fraction=0,
            allowed_dates={date(2026, 1, 5)},
        )
