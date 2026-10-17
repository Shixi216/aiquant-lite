from __future__ import annotations

from datetime import date

from data_hub.services.capital_flow_enhanced import _pv_score
from trading.schemas import Bar


def _bars(*, price_up: bool, volume_up: bool) -> list[Bar]:
    first = Bar(
        trade_date=date(2026, 9, 1), open=10, high=10.2, low=9.8,
        close=10, volume=2_000,
    )
    close = 10.2 if price_up else 9.0
    volume = 3_000 if volume_up else 1_000
    second = Bar(
        trade_date=date(2026, 9, 2), open=10, high=max(10, close),
        low=min(10, close), close=close, volume=volume,
    )
    return [first, second]


def test_high_turnover_down_day_is_never_labeled_volume_contraction() -> None:
    result = _pv_score(
        _bars(price_up=False, volume_up=False),
        {"turnover_percentile_20d": 0.90},
    )
    assert result["pv_label"] == "高换手（近20日分位90%）"
    assert "缩量" not in result["pv_label"]


def test_middle_turnover_percentile_keeps_original_price_volume_label() -> None:
    result = _pv_score(
        _bars(price_up=False, volume_up=False),
        {"turnover_percentile_20d": 0.50},
    )
    assert result["pv_label"] == "价跌量缩"
