from trading.experiments.overheat_penalty_shadow import (
    PenaltyComponent, component_penalty, shadow_score, top_fraction,
)


def test_component_penalty_starts_only_at_research_q75():
    component = PenaltyComponent("x", median=.10, q75=.20, weight=.05)
    assert component_penalty(.19, component) == 0
    assert component_penalty(.20, component) == .05


def test_component_penalty_is_capped_without_parameter_search():
    component = PenaltyComponent("x", median=.10, q75=.20, weight=.05)
    assert component_penalty(10, component) == .10


def test_shadow_score_subtracts_only_two_accumulated_overheat_components():
    from trading.experiments.overheat_penalty_shadow import PenaltyCalibration
    calibration = PenaltyCalibration(
        expansion=PenaltyComponent("ma20_ma60_expansion", .1, .2, .05),
        ma60_slope=PenaltyComponent("ma60_slope_5", .01, .02, .04),
    )
    row = {
        "technical_score": .8, "ma20_ma60_expansion": .2,
        "ma60_slope_5": .02, "expansion_speed_5": 99,
    }
    result = shadow_score(row, calibration)
    assert abs(result["shadow_technical_score"] - .71) < 1e-12
    assert "expansion_speed" not in " ".join(result)


def test_top_fraction_orders_by_requested_score():
    rows = [{"id": i, "score": value} for i, value in enumerate((.1, .9, .4, .8))]
    assert [row["id"] for row in top_fraction(rows, "score", .5)] == [1, 3]
