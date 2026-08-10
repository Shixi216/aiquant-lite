from trading.scanner.production_partition import (
    CandidateLayer,
    classify_daily_candidate,
)


def test_core_requires_both_frozen_conditions() -> None:
    assert classify_daily_candidate(
        price=12.0,
        sma20=11.0,
        sma60=10.0,
        technical_score=0.31,
    ) == CandidateLayer.CORE


def test_near_hits_exactly_one_condition() -> None:
    assert classify_daily_candidate(
        price=12.0,
        sma20=11.0,
        sma60=10.0,
        technical_score=0.30,
    ) == CandidateLayer.NEAR
    assert classify_daily_candidate(
        price=9.0,
        sma20=11.0,
        sma60=10.0,
        technical_score=0.31,
    ) == CandidateLayer.NEAR


def test_control_hits_neither_condition() -> None:
    assert classify_daily_candidate(
        price=9.0,
        sma20=11.0,
        sma60=10.0,
        technical_score=0.30,
    ) == CandidateLayer.CONTROL