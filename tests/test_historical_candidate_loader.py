from datetime import date, timedelta

from trading.experiments.historical_candidate_loader import (
    eligible_signal_dates,
    month_end_signal_dates,
)


def test_month_end_dates_respect_warmup_and_forward_windows() -> None:
    start = date(2025, 1, 1)
    dates = [start + timedelta(days=index) for index in range(140)]
    selected = month_end_signal_dates(
        dates,
        history_days=60,
        forward_days=20,
    )
    assert selected
    assert selected[0] >= dates[59]
    assert selected[-1] <= dates[-21]
    assert len({(item.year, item.month) for item in selected}) == len(selected)


def test_month_end_dates_require_complete_windows() -> None:
    dates = [date(2025, 1, 1) + timedelta(days=index) for index in range(79)]
    assert month_end_signal_dates(dates) == []

def test_eligible_signal_dates_keep_every_valid_trading_day():
    days = [date(2025, 1, 1) + timedelta(days=index) for index in range(100)]
    assert eligible_signal_dates(days) == days[59:-20]
    assert len(eligible_signal_dates(days)) == 21