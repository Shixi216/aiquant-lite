from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest
from fastapi.testclient import TestClient

from config.settings import settings
from database.migrations.v0107_historical_market_expansion import (
    apply_migration as apply_v0107,
)
from database.migrations.v0114_formal_history_validation import (
    apply_migration as apply_v0114,
)
from router.api.app import app

client = TestClient(app)

BAR_COLUMNS = """
    bar_id, symbol, trade_date, event_time, data_available_time, data_cutoff,
    generated_at, adjustment_type, open, high, low, close, volume, amount,
    volume_unit, amount_unit, primary_source, source_record_ids_json,
    verification_source_ids_json, verification_status, confidence,
    content_hash, algorithm_version, raw_payload_json
"""

FACTOR_COLUMNS = """
    factor_id, symbol, trade_date, adj_factor, event_time,
    data_available_time, data_cutoff, fetched_at, source, content_hash,
    raw_payload_json
"""


def _prepare(tmp_path, monkeypatch) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "hist-bars.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    connection = duckdb.connect(str(db_path))
    apply_v0107(connection)
    apply_v0114(connection)
    return connection


def _bar(
    bar_id: str,
    symbol: str,
    trade_date: date,
    *,
    close: float,
    adjustment_type: str = "RAW",
    generated_at: datetime | None = None,
) -> list:
    generated_at = generated_at or datetime(2026, 8, 1, 12, 0)
    event = datetime.combine(trade_date, datetime.min.time())
    return [
        bar_id, symbol, trade_date, event, event, event, generated_at,
        adjustment_type, close * 0.98, close * 1.02, close * 0.97, close,
        1_000_000.0, 10_000_000.0, "SHARE", "CNY", "Tushare Pro", "[]", "[]",
        "SINGLE_SOURCE", 0.9, f"hash-{bar_id}", "v1", "{}",
    ]


def _factor(
    factor_id: str,
    symbol: str,
    trade_date: date,
    adj_factor: float,
    *,
    fetched_at: datetime | None = None,
) -> list:
    fetched_at = fetched_at or datetime(2026, 8, 1, 12, 0)
    event = datetime.combine(trade_date, datetime.min.time())
    return [
        factor_id, symbol, trade_date, adj_factor, event, event, event,
        fetched_at, "Tushare Pro", f"hash-{factor_id}", "{}",
    ]


def _seed_symbol(
    connection: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    days: int = 70,
    with_factors: bool = True,
    factors: dict[date, float] | None = None,
) -> tuple[list[date], list[float], dict[date, float]]:
    """Insert one RAW row per day plus an older RAW duplicate and a
    FORWARD_ADJUSTED decoy for the middle day (dedup semantics)."""
    start = date(2026, 5, 1)
    dates = [start + timedelta(days=index) for index in range(days)]
    closes = [10.0 + 0.1 * index for index in range(days)]
    if factors is None:
        factors = {
            trade_date: 1.0 + 0.01 * index for index, trade_date in enumerate(dates)
        }
    rows = []
    for index, trade_date in enumerate(dates):
        rows.append(
            _bar(
                f"{symbol}-{index}-old",
                symbol,
                trade_date,
                close=closes[index] + 5.0,  # stale decoy, older generated_at
                generated_at=datetime(2026, 8, 1, 11, 0),
            )
        )
        rows.append(_bar(f"{symbol}-{index}", symbol, trade_date, close=closes[index]))
    middle = days // 2
    rows.append(
        _bar(
            f"{symbol}-{middle}-fwd",
            symbol,
            dates[middle],
            close=999.0,
            adjustment_type="FORWARD_ADJUSTED",
            generated_at=datetime(2026, 8, 9, 0, 0),
        )
    )
    connection.executemany(
        f"INSERT INTO canonical_historical_bars ({BAR_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    if with_factors:
        factor_rows = [
            _factor(f"f-{symbol}-{index}", symbol, trade_date, factor)
            for index, (trade_date, factor) in enumerate(factors.items())
        ]
        connection.executemany(
            f"INSERT INTO historical_adjustment_factors ({FACTOR_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            factor_rows,
        )
    return dates, closes, factors


def _assert_matches_production_formula(
    bars: list[dict],
    closes: list[float],
    factors: dict[date, float],
    returned_dates: list[date],
    offset: int,
) -> None:
    anchor_date = max(factors)
    base_factor = factors[anchor_date]
    for index, bar in enumerate(bars):
        source_index = offset + index
        ratio = factors[returned_dates[index]] / base_factor
        assert bar["close"] == pytest.approx(closes[source_index] * ratio)
        assert bar["open"] == pytest.approx(closes[source_index] * 0.98 * ratio)
        # volume / amount must never be rescaled
        assert bar["volume"] == 1_000_000.0
        assert bar["amount"] == 10_000_000.0


def test_historical_bars_qfq_matches_production_formula(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    dates, closes, factors = _seed_symbol(connection, "600519.SH")
    connection.close()

    response = client.get("/v1/historical-bars/600519.SH", params={"days": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "600519.SH"
    assert body["requested_adjustment_type"] == "FORWARD_ADJUSTED"
    assert body["effective_adjustment_type"] == "FORWARD_ADJUSTED"
    assert body["adjustment_fallback"] is False
    assert body["factor_anchor_date"] == str(max(factors))
    assert body["bar_count"] == 10
    returned_dates = [date.fromisoformat(bar["trade_date"]) for bar in body["bars"]]
    assert returned_dates == dates[-10:]

    base_factor = factors[max(factors)]
    for index, bar in enumerate(body["bars"]):
        source_index = len(closes) - 10 + index
        ratio = factors[returned_dates[index]] / base_factor
        assert bar["close"] == pytest.approx(closes[source_index] * ratio)
        assert bar["open"] == pytest.approx(closes[source_index] * 0.98 * ratio)
        assert bar["high"] == pytest.approx(closes[source_index] * 1.02 * ratio)
        assert bar["low"] == pytest.approx(closes[source_index] * 0.97 * ratio)
        assert bar["volume"] == pytest.approx(1_000_000.0)
        assert bar["amount"] == pytest.approx(10_000_000.0)
        previous = closes[source_index - 1] * factors[dates[source_index - 1]] / base_factor
        assert bar["change_pct"] == pytest.approx((bar["close"] / previous - 1) * 100.0)


def test_historical_bars_symbol_normalization_exact_match(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    _seed_symbol(connection, "600519.SH")
    # decoy whose code contains 600519 as a substring
    connection.execute(
        f"INSERT INTO canonical_historical_bars ({BAR_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _bar("decoy", "6005190.SH", date(2026, 5, 1), close=77.0),
    )
    connection.close()

    bare = client.get("/v1/historical-bars/600519", params={"days": 5})
    canonical = client.get("/v1/historical-bars/600519.SH", params={"days": 5})
    assert bare.status_code == 200
    assert canonical.status_code == 200
    assert bare.json()["symbol"] == "600519.SH"
    assert [bar["trade_date"] for bar in bare.json()["bars"]] == [
        bar["trade_date"] for bar in canonical.json()["bars"]
    ]

    # invalid code: normalize fails
    invalid = client.get("/v1/historical-bars/not-a-code")
    assert invalid.status_code == 422
    # substring decoy: 7-digit code is not a valid A-share symbol and must
    # never be reachable through the 600519 candidate set
    decoy = client.get("/v1/historical-bars/6005190.SH")
    assert decoy.status_code == 422
    bare_decoy = client.get("/v1/historical-bars/6005190")
    assert bare_decoy.status_code == 422


def test_historical_bars_raw_request(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    _seed_symbol(connection, "000001.SZ")
    connection.close()

    response = client.get(
        "/v1/historical-bars/000001.SZ",
        params={"days": 5, "adjustment_type": "RAW"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["effective_adjustment_type"] == "RAW"
    assert body["adjustment_fallback"] is False
    assert body["bars"][0]["close"] == 10.0 + 0.1 * 65


def test_historical_bars_factor_missing_falls_back_to_raw(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    _seed_symbol(connection, "000012.SZ", with_factors=False)
    connection.close()

    response = client.get("/v1/historical-bars/000012.SZ", params={"days": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["effective_adjustment_type"] == "RAW"
    assert body["adjustment_fallback"] is True
    assert body["adjustment_fallback_reason"] == "missing_adjustment_factors"
    assert body["factor_anchor_date"] is None
    assert body["bars"][0]["close"] == 10.0 + 0.1 * 65


def test_historical_bars_partial_factor_window_falls_back(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    dates, closes, factors = _seed_symbol(connection, "000026.SZ")
    # drop one factor inside the fetched window -> incomplete
    missing = dates[20]
    connection.execute(
        "DELETE FROM historical_adjustment_factors WHERE trade_date = ?",
        [missing],
    )
    connection.close()

    response = client.get("/v1/historical-bars/000026.SZ", params={"days": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["effective_adjustment_type"] == "RAW"
    assert body["adjustment_fallback"] is True
    assert body["adjustment_fallback_reason"] == "factor_window_incomplete"


def test_historical_bars_ma60_warmup_and_days_slice(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    _seed_symbol(connection, "600177.SH", days=70)
    connection.close()

    response = client.get("/v1/historical-bars/600177.SH", params={"days": 10})
    bars = response.json()["bars"]
    assert all(bar["ma60"] is not None for bar in bars)
    assert all(bar["ma5"] is not None for bar in bars)

    short = client.get("/v1/historical-bars/600177.SH", params={"days": 5})
    assert short.status_code == 200
    body = short.json()
    assert body["bar_count"] == 5
    assert body["bars"][0]["trade_date"] == str(date(2026, 5, 1) + timedelta(days=65))


def test_historical_bars_unknown_symbol_404(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    _seed_symbol(connection, "600519.SH")
    connection.close()

    response = client.get("/v1/historical-bars/300750.SZ")
    assert response.status_code == 404


def test_historical_bars_days_validation(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    assert client.get("/v1/historical-bars/600519.SH", params={"days": 4}).status_code == 422
    assert client.get("/v1/historical-bars/600519.SH", params={"days": 251}).status_code == 422
    assert (
        client.get(
            "/v1/historical-bars/600519.SH",
            params={"adjustment_type": "BACKWARD_ADJUSTED"},
        ).status_code
        == 422
    )
