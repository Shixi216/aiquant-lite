from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from trading.research.strategy_lab.dataset_builder import _technical_group
from trading.research.technical.analysis import technical_signal
from trading.schemas import Bar


def test_vectorized_technical_packet_matches_frozen_signal() -> None:
    start = date(2024, 1, 1)
    closes = 10.0 + np.sin(np.arange(180) / 11.0) + np.arange(180) * 0.01
    frame = pd.DataFrame(
        {
            "symbol": ["000001.SZ"] * 180,
            "trade_date": [start + timedelta(days=i) for i in range(180)],
            "close": closes,
            "volume": np.arange(180, dtype=float) + 1000.0,
        }
    )
    result = _technical_group(frame)
    for index in (59, 73, 120, 179):
        bars = [
            Bar(
                trade_date=frame.iloc[i].trade_date,
                open=float(frame.iloc[i].close),
                high=float(frame.iloc[i].close),
                low=float(frame.iloc[i].close),
                close=float(frame.iloc[i].close),
                volume=float(frame.iloc[i].volume),
            )
            for i in range(index - 59, index + 1)
        ]
        expected = technical_signal(bars)
        assert result.iloc[index].technical_score == pytest.approx(expected.score, abs=1e-12)
        assert result.iloc[index].technical_confidence == pytest.approx(expected.confidence)


def test_technical_packet_is_unavailable_before_sixty_bars() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["000001.SZ"] * 59,
            "trade_date": pd.date_range("2024-01-01", periods=59),
            "close": np.arange(59, dtype=float) + 10.0,
            "volume": np.ones(59),
        }
    )
    result = _technical_group(frame)
    assert not result.technical_available.any()
    assert result.technical_score.isna().all()
