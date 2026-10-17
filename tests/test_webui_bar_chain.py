from __future__ import annotations

import pytest

from webui.providers import bar_chain, local_bars, quote_tail
from webui.providers.local_bars import LocalBarsResult


def _local_result(last_date: str, *, days: int = 10) -> LocalBarsResult:
    bars = []
    for index in range(days):
        day_offset = days - index
        bars.append(
            {
                "trade_date": _shift(last_date, -day_offset + 1),
                "open": 10.0 + index * 0.1,
                "high": 10.5 + index * 0.1,
                "low": 9.8 + index * 0.1,
                "close": 10.2 + index * 0.1,
                "volume": 1_000.0,
                "amount": 10_000.0,
                "adj_factor": 1.0 + 0.01 * index,
            }
        )
    return LocalBarsResult(
        symbol="600519.SH", bars=bars, last_trade_date=last_date
    )


def _shift(iso: str, days: int) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def _no_real_db(monkeypatch):
    monkeypatch.setattr(
        local_bars, "full_market_coverage_date", lambda: "2026-08-06"
    )


def test_local_replay_primary_no_tail_when_fresh(monkeypatch):
    # local last date == today -> tail not triggered
    from datetime import date

    today = date.today().isoformat()
    calls = {"fetch": 0}

    def fake_fetch(symbol, *, limit=400):
        calls["fetch"] += 1
        return _local_result(today)

    monkeypatch.setattr(local_bars, "fetch_local_raw_bars", fake_fetch)

    async def fake_tail(*args, **kwargs):
        raise AssertionError("tail must not be triggered")

    monkeypatch.setattr(quote_tail, "fetch_tail", fake_tail)

    payload = bar_chain.build_chart(
        "600519", days=5, router_client=None
    )
    import asyncio

    result = asyncio.run(payload)
    assert result.data_source == "local_replay_history"
    assert result.formal_status == "fact"
    assert result.tail_fill["triggered"] is False
    assert result.adjustment["effective_adjustment_type"] == "FORWARD_ADJUSTED"
    assert result.adjustment["factor_anchor_date"] == today
    assert len(result.bars) == 5

    # expected values via the production QFQ formula:
    # adj_i = raw_i * f_i / f_last  (anchor = latest factor, f_9 = 1.09)
    def adj_close(i: float) -> float:
        return (10.2 + 0.1 * i) * (1.0 + 0.01 * i) / 1.09

    last_two = (adj_close(9) / adj_close(8) - 1) * 100.0
    assert result.bars[-1]["change_pct"] == pytest.approx(last_two)
    assert result.bars[-1]["close"] == pytest.approx(adj_close(9))
    ma5 = sum(adj_close(i) for i in range(5, 10)) / 5
    assert result.bars[-1]["ma5"] == pytest.approx(ma5)


def test_tail_fill_only_missing_days(monkeypatch):
    local_last = "2026-08-06"
    tail_request: dict = {}

    monkeypatch.setattr(
        local_bars, "fetch_local_raw_bars", lambda s, *, limit=400: _local_result(local_last)
    )

    async def fake_tail(symbol, *, start_date, end_date):
        tail_request["symbol"] = symbol
        tail_request["start_date"] = start_date
        tail_request["end_date"] = end_date
        return quote_tail.TailBars(
            bars=[
                {
                    "trade_date": "2026-08-07",
                    "open": 11.2,
                    "high": 11.5,
                    "low": 11.0,
                    "close": 11.3,
                    "volume": 2_000.0,
                    "amount": 20_000.0,
                    "adj_factor": None,
                }
            ],
            factors={"2026-08-07": 1.09},
        )

    monkeypatch.setattr(quote_tail, "fetch_tail", fake_tail)

    result = asyncio_run(bar_chain.build_chart("600519.SH", days=20, router_client=None))
    assert tail_request["start_date"] == "2026-08-07"
    assert tail_request["end_date"] >= local_last
    assert tail_request["symbol"] == "600519.SH"
    assert result.tail_fill == {
        "triggered": True,
        "ok": True,
        "reason_code": "ok",
    }
    assert result.data_source == "local_replay_history+provider_tail_fill"
    assert result.bars[-1]["trade_date"] == "2026-08-07"
    assert result.data_time == "2026-08-07"


def test_seam_check_rejects_adjustment_break(monkeypatch):
    monkeypatch.setattr(
        local_bars, "fetch_local_raw_bars", lambda s, *, limit=400: _local_result("2026-08-06")
    )

    async def fake_tail(symbol, *, start_date, end_date):
        # tail factors on a doubled scale -> QFQ-adjusted seam jump ~+100%
        return quote_tail.TailBars(
            bars=[
                {
                    "trade_date": "2026-08-07",
                    "open": 11.2,
                    "high": 11.5,
                    "low": 11.0,
                    "close": 11.3,
                    "volume": 2_000.0,
                    "amount": 20_000.0,
                    "adj_factor": None,
                }
            ],
            factors={"2026-08-07": 2.18},
        )

    monkeypatch.setattr(quote_tail, "fetch_tail", fake_tail)

    result = asyncio_run(bar_chain.build_chart("600519.SH", days=20, router_client=None))
    assert result.tail_fill["ok"] is False
    assert result.tail_fill["reason_code"] == "seam_rejected"
    assert "gap" in result.tail_fill["detail"]
    # local bars only, nothing spliced
    assert all(bar["trade_date"] < "2026-08-07" for bar in result.bars)
    assert result.data_source == "local_replay_history"
    assert result.adjustment["effective_adjustment_type"] == "FORWARD_ADJUSTED"


def test_seam_check_allows_legitimate_ex_dividend(monkeypatch):
    monkeypatch.setattr(
        local_bars, "fetch_local_raw_bars", lambda s, *, limit=400: _local_result("2026-08-06")
    )

    async def fake_tail(symbol, *, start_date, end_date):
        # ex-div day: factor doubled AND raw price halved -> adjusted series
        # is continuous, so the merge must be ACCEPTED
        return quote_tail.TailBars(
            bars=[
                {
                    "trade_date": "2026-08-07",
                    "open": 5.6,
                    "high": 5.8,
                    "low": 5.5,
                    "close": 5.7,
                    "volume": 2_000.0,
                    "amount": 20_000.0,
                    "adj_factor": None,
                }
            ],
            factors={"2026-08-07": 2.18},
        )

    monkeypatch.setattr(quote_tail, "fetch_tail", fake_tail)

    result = asyncio_run(bar_chain.build_chart("600519.SH", days=20, router_client=None))
    assert result.tail_fill["ok"] is True
    assert result.bars[-1]["trade_date"] == "2026-08-07"
    assert result.data_source == "local_replay_history+provider_tail_fill"


def test_tail_with_missing_factors_rejects_merge(monkeypatch):
    monkeypatch.setattr(
        local_bars, "fetch_local_raw_bars", lambda s, *, limit=400: _local_result("2026-08-06")
    )

    async def fake_tail(symbol, *, start_date, end_date):
        return quote_tail.TailBars(
            bars=[
                {
                    "trade_date": "2026-08-07",
                    "open": 11.2,
                    "high": 11.5,
                    "low": 11.0,
                    "close": 11.3,
                    "volume": 2_000.0,
                    "amount": 20_000.0,
                    "adj_factor": None,
                }
            ],
            factors={},  # no factor rows -> cannot prove consistency
        )

    monkeypatch.setattr(quote_tail, "fetch_tail", fake_tail)

    result = asyncio_run(bar_chain.build_chart("600519.SH", days=20, router_client=None))
    assert result.tail_fill["ok"] is False
    assert result.tail_fill["detail"] == "tail_adjustment_factors_missing"
    assert all(bar["trade_date"] < "2026-08-07" for bar in result.bars)


def test_provider_failure_degrades_honestly(monkeypatch):
    monkeypatch.setattr(
        local_bars, "fetch_local_raw_bars", lambda s, *, limit=400: _local_result("2026-08-06")
    )

    async def fail_tail(symbol, *, start_date, end_date):
        return None

    monkeypatch.setattr(quote_tail, "fetch_tail", fail_tail)

    result = asyncio_run(bar_chain.build_chart("600519.SH", days=20, router_client=None))
    assert result.tail_fill == {
        "triggered": True,
        "ok": False,
        "reason_code": "provider_failed",
    }
    assert result.bars[-1]["trade_date"] == "2026-08-06"
    assert result.data_time == "2026-08-06"
    assert result.market_coverage_date == "2026-08-06"


def test_tail_cache_prevents_second_provider_call(monkeypatch):
    calls = {"count": 0}

    def fake_sync(symbol, start, end):
        calls["count"] += 1
        return quote_tail.TailBars(
            bars=[
                {
                    "trade_date": "2026-08-07",
                    "open": 11.2,
                    "high": 11.5,
                    "low": 11.0,
                    "close": 11.3,
                    "volume": 2_000.0,
                    "amount": 20_000.0,
                    "adj_factor": 1.09,
                }
            ],
            factors={"2026-08-07": 1.09},
        )

    monkeypatch.setattr(quote_tail, "_provider_bars_sync", fake_sync)
    quote_tail.clear_cache()

    import asyncio

    first = asyncio.run(
        quote_tail.fetch_tail("600519.SH", start_date="2026-08-07", end_date="2026-08-20")
    )
    second = asyncio.run(
        quote_tail.fetch_tail("600519.SH", start_date="2026-08-07", end_date="2026-08-20")
    )
    assert calls["count"] == 1
    assert first is second
    quote_tail.clear_cache()


def test_backup_fallback_disabled_by_default(monkeypatch):
    monkeypatch.setattr(local_bars, "ENABLE_BACKUP_BAR_FALLBACK", False)

    def boom(db_path, candidates, limit):
        raise AssertionError("backup path must not be queried by default")

    monkeypatch.setattr(local_bars, "_query_backup", boom)
    monkeypatch.setattr(
        local_bars,
        "_query_replay",
        lambda db_path, candidates, limit: [],
    )
    result = local_bars.fetch_local_raw_bars("600519.SH")
    assert result.bars == []


def test_router_chain_first_when_available(monkeypatch):
    monkeypatch.setattr(
        local_bars, "fetch_local_raw_bars", lambda s, *, limit=400: _local_result("2026-08-06")
    )

    class FakeResult:
        ok = True
        status = 200
        data = {
            "symbol": "600519.SH",
            "bars": [
                {
                    "trade_date": "2026-08-06",
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                    "amount": 1.0,
                    "change_pct": None,
                    "ma5": None,
                    "ma10": None,
                    "ma20": None,
                    "ma60": None,
                }
            ],
            "last_trade_date": "2026-08-06",
            "requested_adjustment_type": "FORWARD_ADJUSTED",
            "effective_adjustment_type": "FORWARD_ADJUSTED",
            "adjustment_fallback": False,
            "adjustment_fallback_reason": None,
            "factor_anchor_date": "2026-08-06",
        }
        reason_code = None

    class FakeClient:
        pass

    async def fake_fetch(client, path, params=None, *, timeout=10.0):
        return FakeResult()

    monkeypatch.setattr(bar_chain, "fetch_json", fake_fetch)

    result = asyncio_run(bar_chain.build_chart("600519", days=5, router_client=FakeClient()))
    assert result.data_source == "router_canonical_history"
    assert result.bars[0]["trade_date"] == "2026-08-06"


def asyncio_run(coroutine):
    import asyncio

    return asyncio.run(coroutine)


def test_tail_normalizes_provider_units(monkeypatch):
    from types import SimpleNamespace
    from data_hub.providers.tushare_provider import TushareProvider

    from config.settings import settings
    monkeypatch.setattr(settings, "tushare_token", "offline-test-token")

    monkeypatch.setattr(TushareProvider, "get_daily_bars", lambda *a, **k: [
        SimpleNamespace(data={"trade_date": "20260904", "open": 10, "high": 11,
            "low": 9, "close": 10, "vol": 1234.5, "amount": 1234.5})
    ])
    monkeypatch.setattr(TushareProvider, "get_adjustment_factors", lambda *a, **k: [
        {"trade_date": "20260904", "adj_factor": 1.0}
    ])
    result = quote_tail._provider_bars_sync("000001.SZ", "2026-09-04", "2026-09-04")
    assert result.bars[0]["volume"] == 123450.0
    assert result.bars[0]["amount"] == 1234500.0


def test_missing_replay_degrades_to_empty(monkeypatch):
    import duckdb

    def unavailable(*args):
        raise duckdb.IOException("missing sidecar")

    monkeypatch.setattr(local_bars, "_query_replay", unavailable)
    monkeypatch.setattr(local_bars, "ENABLE_BACKUP_BAR_FALLBACK", False)
    assert local_bars.fetch_local_raw_bars("000001.SZ").bars == []
