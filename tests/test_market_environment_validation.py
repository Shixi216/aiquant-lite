from datetime import date, timedelta

from trading.experiments.market_environment_validation import (
    MarketState,
    chronological_split,
    condition_flags,
)


def test_condition_flags_use_natural_thresholds():
    state = MarketState(
        trade_date=date(2025, 1, 2), csi300_close=110,
        csi300_ma20=105, csi300_ma60=100, csi300_return20=.01,
        advancers=3000, decliners=2000, above_ma20_ratio=.6,
        amount_ratio20=1.1,
    )
    assert all(value is True for value in condition_flags(state).values())


def test_chronological_split_has_embargo_and_no_overlap():
    start = date(2025, 1, 1)
    dates = [start + timedelta(days=index) for index in range(100)]
    research, embargo, validation = chronological_split(dates, embargo_dates=20)
    assert len(research) == 60
    assert len(embargo) == 20
    assert len(validation) == 20
    assert research[-1] < embargo[0] < validation[0]
    assert not (set(research) & set(validation))


def test_missing_moving_average_is_not_imputed():
    state = MarketState(
        trade_date=date(2025, 1, 2), csi300_close=110,
        csi300_ma20=None, csi300_ma60=None, csi300_return20=None,
        advancers=0, decliners=0, above_ma20_ratio=None,
        amount_ratio20=None,
    )
    flags = condition_flags(state)
    assert flags["csi300_above_ma20"] is None
    assert flags["above_ma20_majority"] is None
