from __future__ import annotations

import json
from datetime import date

import pytest

from trading.experiments.formal_replay_loader import (
    _aligned_returns,
    load_candidate_file,
)
from trading.scanner.production_partition import CandidateLayer


def test_load_candidate_file_keeps_production_layer(tmp_path):
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps([{
        "symbol": "000001.SZ", "signal_date": "2025-10-31",
        "layer": "CORE", "price": 12.0, "amount": 400_000_000,
        "technical_score": 0.4, "sma20": 11.0, "sma60": 10.0,
    }]), encoding="utf-8")
    rows = load_candidate_file(path)
    assert len(rows) == 1
    assert rows[0].layer is CandidateLayer.CORE
    assert rows[0].signal_date == date(2025, 10, 31)


def test_aligned_returns_use_next_trade_open_and_exact_exit_dates():
    days = [date(2025, 11, 3), date(2025, 11, 4), date(2025, 11, 5)]
    by_horizon, by_date = _aligned_returns(days, {
        days[0]: (100.0, 101.0),
        days[1]: (102.0, 103.0),
        days[2]: (104.0, 105.0),
    })
    assert by_horizon[1] == pytest.approx(0.01)
    assert by_horizon[3] == pytest.approx(0.05)
    assert by_date[days[1]] == pytest.approx(0.03)