from __future__ import annotations

import copy
import inspect
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import data_hub.repositories.history as history_repository_module
from config.settings import settings
from database.db import get_connection, initialize_database
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.history import (
    HistoryBackfillRequest,
    HistoryRunActionRequest,
    SelectionStrategy,
    ShardType,
)
from data_hub.services.full_market_common import stable_hash
from data_hub.services.history_backfill_service import HistoryBackfillService
from data_hub.services.history_coverage_service import HistoryCoverageService
from data_hub.services.trade_date_history_service import (
    TUSHARE_DAILY_NORMALIZATION_VERSION,
    TradeDateHistoryBackfillService,
)
from trading.decision_support.orchestrator.service import _generate_strategy
from trading.research.capital_flow.policy import policy_for as capital_policy
from trading.research.policy_news.policy import policy_for as policy_news_policy
from trading.research.sentiment.policy import policy_for as sentiment_policy
from trading.schemas import DecisionPacket
from data_hub.schemas.full_market import AnalysisMode
from scripts.history_cli import build_parser


TZ = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime(2026, 7, 29, 20, tzinfo=TZ)
SYMBOLS = ("600000.SH", "000001.SZ", "920001.BJ")


class TickClock:
    def __init__(self) -> None:
        self.value = CUTOFF

    def __call__(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


class FakeTushareDaily:
    def __init__(self) -> None:
        self.calls: list[date] = []
        self.fail_dates: set[date] = set()
        self.empty_dates: set[date] = set()
        self.omit: dict[date, set[str]] = {}
        self.invalid_ohlc: dict[date, set[str]] = {}

    def get_daily_by_trade_date(
        self,
        trade_date: date,
    ) -> list[dict[str, Any]]:
        self.calls.append(trade_date)
        if trade_date in self.fail_dates:
            raise ConnectionError("bounded provider failure")
        if trade_date in self.empty_dates:
            return []
        rows: list[dict[str, Any]] = []
        for symbol in SYMBOLS:
            if symbol in self.omit.get(trade_date, set()):
                continue
            high = 8.0 if symbol in self.invalid_ohlc.get(
                trade_date,
                set(),
            ) else 11.0
            rows.append(
                {
                    "ts_code": symbol,
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "open": 10.0,
                    "high": high,
                    "low": 9.0,
                    "close": 10.5,
                    "pre_close": 10.0,
                    "change": 0.5,
                    "pct_chg": 5.0,
                    "vol": 123.0,
                    "amount": 456.0,
                }
            )
        return copy.deepcopy(rows)


def _seed_universe() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO stock_universe_versions
            VALUES (
                'date-test-v1', 'date-test-hash', ?, ?, ?, 3, 3,
                '["TEST"]', '{}'
            )
            """,
            [CUTOFF - timedelta(days=1)] * 3,
        )
        connection.executemany(
            """
            INSERT INTO stock_universe (
                symbol, exchange, market, board, security_type,
                company_name, short_name, list_date, delist_date,
                listing_status, is_st, is_suspended, currency,
                price_limit_type, primary_source, source_record_ids_json,
                verification_status, source_differences_json,
                data_available_time, updated_at, universe_version
            )
            VALUES (
                ?, ?, 'CN_A', ?, 'STOCK', ?, ?, '2000-01-01', NULL,
                'ACTIVE', FALSE, FALSE, 'CNY', 'STANDARD', 'TEST', '[]',
                'SINGLE_SOURCE', '{}', ?, ?, 'date-test-v1'
            )
            """,
            [
                [
                    symbol,
                    symbol.rsplit(".", 1)[1],
                    "BEIJING" if symbol.endswith(".BJ") else "MAIN",
                    symbol,
                    symbol,
                    CUTOFF - timedelta(days=1),
                    CUTOFF - timedelta(days=1),
                ]
                for symbol in SYMBOLS
            ],
        )


def _seed_calendar(repository: HistoryRepository) -> list[date]:
    start = CUTOFF.date() - timedelta(days=100)
    rows: list[dict[str, Any]] = []
    current = start
    while current <= CUTOFF.date():
        rows.append(
            {
                "calendar_date": current,
                "is_trading_day": current.weekday() < 5,
                "content_hash": stable_hash(
                    {"calendar_date": current, "provider": "TEST"}
                ),
                "metadata": {"provider": "TEST"},
            }
        )
        current += timedelta(days=1)
    repository.save_calendar(
        provider="TEST",
        rows=rows,
        data_cutoff=CUTOFF - timedelta(microseconds=1),
        fetched_at=CUTOFF - timedelta(microseconds=1),
    )
    return repository.trading_days(data_cutoff=CUTOFF, limit=60)


@pytest.fixture
def date_history_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    path = tmp_path / "history-0108.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    monkeypatch.setattr(settings, "tushare_token", "")
    initialize_database()
    _seed_universe()
    repository = HistoryRepository()
    days = _seed_calendar(repository)
    provider = FakeTushareDaily()
    clock = TickClock()
    service = HistoryBackfillService(
        repository=repository,
        providers={"TUSHARE": provider},
        clock=clock,
    )
    return {
        "repository": repository,
        "days": days,
        "provider": provider,
        "service": service,
        "clock": clock,
    }


def _request(
    dates: list[date],
    *,
    dry_run: bool = False,
    budget: int | None = None,
    batch_size: int | None = None,
    verify_idempotency: bool = False,
) -> HistoryBackfillRequest:
    return HistoryBackfillRequest(
        dry_run=dry_run,
        shard_type=ShardType.TRADE_DATE_SHARD,
        provider="TUSHARE",
        fallback_providers=[],
        trade_dates=dates,
        target_trading_days=len(dates),
        concurrency=1,
        batch_size=batch_size or max(1, len(dates)),
        request_budget=len(dates) if budget is None else budget,
        max_retries=0,
        data_cutoff=CUTOFF,
        selection_strategy=SelectionStrategy.TRADE_DATE,
        minimum_free_bytes=0,
        verify_idempotency=verify_idempotency,
    )


def _counts() -> tuple[int, int]:
    with get_connection() as connection:
        return tuple(
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            for table in ("data_records", "canonical_historical_bars")
        )


def test_01_trade_date_shard_plan_is_date_based(
    date_history_db: dict[str, Any],
) -> None:
    result = date_history_db["service"].run(
        _request(date_history_db["days"][-2:], dry_run=True)
    )
    assert result.shard_type == ShardType.TRADE_DATE_SHARD
    assert result.requested_trade_date_count == 2
    assert len(result.date_shards) == 2
    assert date_history_db["provider"].calls == []


def test_02_one_date_calls_provider_once(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    result = date_history_db["service"].run(_request([target]))
    assert result.status == "SUCCESS"
    assert date_history_db["provider"].calls == [target]


def test_03_request_budget_counts_dates(
    date_history_db: dict[str, Any],
) -> None:
    targets = date_history_db["days"][-2:]
    result = date_history_db["service"].run(
        _request(targets, budget=1)
    )
    assert result.status == "PAUSED_BUDGET"
    assert result.request_count == 1
    assert date_history_db["provider"].calls == [targets[0]]


def test_04_tushare_lots_convert_to_shares(
    date_history_db: dict[str, Any],
) -> None:
    date_history_db["service"].run(
        _request([date_history_db["days"][-1]])
    )
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT volume, volume_unit, algorithm_version
            FROM canonical_historical_bars LIMIT 1
            """
        ).fetchone()
    assert row == (
        12_300.0,
        "SHARES",
        TUSHARE_DAILY_NORMALIZATION_VERSION,
    )


def test_05_tushare_thousand_cny_converts_to_cny(
    date_history_db: dict[str, Any],
) -> None:
    date_history_db["service"].run(
        _request([date_history_db["days"][-1]])
    )
    with get_connection() as connection:
        assert connection.execute(
            """
            SELECT amount, amount_unit
            FROM canonical_historical_bars LIMIT 1
            """
        ).fetchone() == (456_000.0, "CNY")


def test_06_raw_payload_is_unchanged(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    expected = FakeTushareDaily().get_daily_by_trade_date(target)[0]
    date_history_db["service"].run(_request([target]))
    with get_connection() as connection:
        value = connection.execute(
            """
            SELECT payload_json FROM data_records
            WHERE symbol = '600000.SH'
            """
        ).fetchone()[0]
    actual = json.loads(value) if isinstance(value, str) else value
    assert actual == expected


def test_07_invalid_ohlc_is_isolated(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["provider"].invalid_ohlc[target] = {"600000.SH"}
    result = date_history_db["service"].run(_request([target]))
    shard = result.date_shards[0]
    assert shard.invalid_record_count == 1
    assert shard.canonical_inserted_count == 2
    assert shard.status == "SUCCESS_PARTIAL"


def test_08_missing_suspended_like_symbol_gets_no_fake_bar(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["provider"].omit[target] = {"600000.SH"}
    date_history_db["service"].run(_request([target]))
    with get_connection() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM canonical_historical_bars
            WHERE symbol = '600000.SH' AND trade_date = ?
            """,
            [target],
        ).fetchone()[0] == 0


def test_09_date_transaction_failure_rolls_back(
    date_history_db: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = history_repository_module._frame_insert

    def fail_canonical(*args: Any, **kwargs: Any) -> None:
        if kwargs.get("table") == "canonical_historical_bars":
            raise RuntimeError("test transaction failure")
        original(*args, **kwargs)

    monkeypatch.setattr(
        history_repository_module,
        "_frame_insert",
        fail_canonical,
    )
    result = date_history_db["service"].run(
        _request([date_history_db["days"][-1]])
    )
    assert result.status == "FAILED"
    assert _counts() == (0, 0)


def test_10_later_date_failure_preserves_prior_success(
    date_history_db: dict[str, Any],
) -> None:
    targets = date_history_db["days"][-2:]
    date_history_db["provider"].fail_dates.add(targets[1])
    result = date_history_db["service"].run(_request(targets))
    assert result.status == "FAILED"
    assert result.date_shards[0].status == "SUCCESS"
    assert _counts() == (3, 3)


def test_11_resume_only_retries_failed_or_pending_date(
    date_history_db: dict[str, Any],
) -> None:
    targets = date_history_db["days"][-2:]
    provider = date_history_db["provider"]
    provider.fail_dates.add(targets[1])
    first = date_history_db["service"].run(_request(targets))
    provider.fail_dates.clear()
    provider.calls.clear()
    result = date_history_db["service"].resume(
        first.run_id,
        HistoryRunActionRequest(
            apply=True,
            data_cutoff=CUTOFF,
            request_budget=1,
        ),
    )
    assert result.status == "SUCCESS"
    assert provider.calls == [targets[1]]


def test_12_successful_date_is_skipped_by_default(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["service"].run(_request([target]))
    date_history_db["provider"].calls.clear()
    result = date_history_db["service"].run(_request([target]))
    assert result.status == "SUCCESS"
    assert result.request_count == 0
    assert date_history_db["provider"].calls == []


def test_13_idempotency_verification_adds_no_logical_rows_or_evidence(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["service"].run(_request([target]))
    with get_connection() as connection:
        before = connection.execute(
            """
            SELECT source_record_ids_json, verification_source_ids_json
            FROM canonical_historical_bars ORDER BY symbol
            """
        ).fetchall()
    result = date_history_db["service"].run(
        _request([target], verify_idempotency=True)
    )
    with get_connection() as connection:
        after = connection.execute(
            """
            SELECT source_record_ids_json, verification_source_ids_json
            FROM canonical_historical_bars ORDER BY symbol
            """
        ).fetchall()
    assert result.persisted_raw_count == 0
    assert result.persisted_canonical_count == 0
    assert before == after


def test_14_existing_baostock_primary_source_is_not_overwritten(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["service"].run(_request([target]))
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE canonical_historical_bars
            SET primary_source = 'BaoStock'
            WHERE symbol = '600000.SH'
            """
        )
    date_history_db["service"].run(
        _request([target], verify_idempotency=True)
    )
    with get_connection() as connection:
        assert connection.execute(
            """
            SELECT primary_source FROM canonical_historical_bars
            WHERE symbol = '600000.SH'
            """
        ).fetchone()[0] == "BaoStock"


def test_15_matching_multi_source_data_keeps_evidence(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["service"].run(_request([target]))
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE canonical_historical_bars
            SET primary_source = 'BaoStock',
                source_record_ids_json = '["bao-record"]',
                verification_source_ids_json = '[]'
            WHERE symbol = '600000.SH'
            """
        )
    date_history_db["service"].run(
        _request([target], verify_idempotency=True)
    )
    with get_connection() as connection:
        status, sources, verification = connection.execute(
            """
            SELECT verification_status, source_record_ids_json,
                   verification_source_ids_json
            FROM canonical_historical_bars
            WHERE symbol = '600000.SH'
            """
        ).fetchone()
    sources = json.loads(sources) if isinstance(sources, str) else sources
    verification = (
        json.loads(verification)
        if isinstance(verification, str)
        else verification
    )
    assert status == "VERIFIED"
    assert "bao-record" in sources
    assert len(verification) == 1


def test_16_conflicting_multi_source_values_are_isolated(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["service"].run(_request([target]))
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE canonical_historical_bars
            SET close = close + 1, primary_source = 'BaoStock'
            WHERE symbol = '600000.SH'
            """
        )
    result = date_history_db["service"].run(
        _request([target], verify_idempotency=True)
    )
    with get_connection() as connection:
        status = connection.execute(
            """
            SELECT verification_status FROM canonical_historical_bars
            WHERE symbol = '600000.SH'
            """
        ).fetchone()[0]
    assert status == "CONFLICT"
    assert result.date_shards[0].conflict_count == 1


def test_17_tushare_failure_does_not_trigger_symbol_fallback(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    provider = date_history_db["provider"]
    provider.fail_dates.add(target)
    result = date_history_db["service"].run(_request([target]))
    assert result.status == "FAILED"
    assert provider.calls == [target]
    assert result.request_count == 1


def test_18_low_return_coverage_is_partial(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["provider"].omit[target] = {"600000.SH"}
    result = date_history_db["service"].run(_request([target]))
    assert result.date_shards[0].coverage_ratio == pytest.approx(2 / 3)
    assert result.date_shards[0].status == "SUCCESS_PARTIAL"


def test_19_beijing_exchange_is_counted(
    date_history_db: dict[str, Any],
) -> None:
    result = date_history_db["service"].run(
        _request([date_history_db["days"][-1]])
    )
    assert result.date_shards[0].beijing_record_count == 1
    with get_connection() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM canonical_historical_bars
            WHERE symbol = '920001.BJ'
            """
        ).fetchone()[0] == 1


def test_20_latest_completed_trade_date_is_audited(
    date_history_db: dict[str, Any],
) -> None:
    repository = date_history_db["repository"]
    assert repository.latest_completed_trade_date(CUTOFF) == (
        date_history_db["days"][-1]
    )
    with pytest.raises(ValueError):
        date_history_db["service"].run(
            _request([CUTOFF.date() + timedelta(days=1)], dry_run=True)
        )


def test_21_fetched_at_does_not_exclude_public_history(
    date_history_db: dict[str, Any],
) -> None:
    days = date_history_db["days"][-20:]
    date_history_db["service"].run(_request(days, budget=20))
    report = HistoryCoverageService(
        repository=date_history_db["repository"]
    ).generate(
        data_cutoff=CUTOFF,
        as_of_trade_date=days[-1],
    )
    assert report.history_20d.numerator == 3
    assert report.required_trade_dates_20d == days


def test_22_date_batch_naturally_repairs_existing_cohort_gap(
    date_history_db: dict[str, Any],
) -> None:
    targets = date_history_db["days"][-2:]
    first = date_history_db["service"].run(_request([targets[0]]))
    assert first.persisted_canonical_count == 3
    second = date_history_db["service"].run(_request([targets[1]]))
    assert second.persisted_canonical_count == 3
    with get_connection() as connection:
        assert connection.execute(
            """
            SELECT COUNT(DISTINCT trade_date)
            FROM canonical_historical_bars
            WHERE symbol = '600000.SH'
            """
        ).fetchone()[0] == 2


def test_23_formal_strategy_weights_remain_60_40() -> None:
    source = inspect.getsource(_generate_strategy)
    assert "technical.score * 0.6" in source
    assert "fundamental.score * 0.4" in source


def test_24_research_factors_remain_shadow() -> None:
    assert sentiment_policy(AnalysisMode.RESEARCH).shadow_mode is True
    assert policy_news_policy(AnalysisMode.RESEARCH).shadow_mode is True
    assert capital_policy(AnalysisMode.RESEARCH).shadow_mode is True


def test_25_decision_packet_remains_immutable() -> None:
    assert DecisionPacket.model_config.get("frozen") is True


def test_26_trade_date_service_has_no_live_or_mass_fallback_path() -> None:
    source = inspect.getsource(TradeDateHistoryBackfillService)
    for forbidden in ("BaoStockProvider", "xtquant", "QMT", "place_order"):
        assert forbidden not in source


def test_27_empty_provider_result_is_not_accepted_as_success(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-1]
    date_history_db["provider"].empty_dates.add(target)
    result = date_history_db["service"].run(_request([target]))
    assert result.status == "PARTIAL"
    assert result.date_shards[0].status == "SUCCESS_EMPTY"


def test_28_cli_accepts_trade_date_shards_and_stays_dry_by_default() -> None:
    parsed = build_parser().parse_args(
        [
            "run_history_backfill",
            "--data-cutoff",
            CUTOFF.isoformat(),
            "--shard-type",
            "trade-date",
            "--trade-date",
            CUTOFF.date().isoformat(),
        ]
    )
    assert parsed.shard_type == ShardType.TRADE_DATE_SHARD
    assert parsed.apply is False


def test_29_openapi_exposes_trade_date_fields(
    date_history_db: dict[str, Any],
) -> None:
    del date_history_db
    from data_hub.api.app import app

    schemas = app.openapi()["components"]["schemas"]
    properties = schemas["HistoryBackfillRequest"]["properties"]
    for field in (
        "shard_type",
        "trade_dates",
        "start_trade_date",
        "end_trade_date",
        "request_budget",
        "max_retries",
        "dry_run",
    ):
        assert field in properties


def test_30_coverage_supports_explicit_as_of_trade_date(
    date_history_db: dict[str, Any],
) -> None:
    target = date_history_db["days"][-2]
    report = HistoryCoverageService(
        repository=date_history_db["repository"]
    ).generate(
        data_cutoff=CUTOFF,
        as_of_trade_date=target,
    )
    assert report.as_of_trade_date == target
    assert report.latest_completed_trade_date == target


def test_31_dry_run_marks_existing_success_without_network_or_write(
    date_history_db: dict[str, Any],
) -> None:
    targets = date_history_db["days"][-2:]
    date_history_db["service"].run(_request([targets[-1]]))
    before = _counts()
    date_history_db["provider"].calls.clear()
    result = date_history_db["service"].run(
        _request(targets, dry_run=True, budget=1)
    )
    assert [item.status for item in result.date_shards] == [
        "PENDING",
        "SUCCESS",
    ]
    assert result.date_shards[-1].payload["skipped_existing_success"] is True
    assert result.completed_trade_date_count == 1
    assert result.request_budget == 1
    assert date_history_db["provider"].calls == []
    assert _counts() == before


def test_32_batch_size_creates_resumable_date_checkpoints(
    date_history_db: dict[str, Any],
) -> None:
    targets = date_history_db["days"][-5:]
    first = date_history_db["service"].run(
        _request(targets, budget=5, batch_size=2)
    )
    assert first.status == "PAUSED_BUDGET"
    assert first.resume_cursor == 2
    assert date_history_db["provider"].calls == targets[:2]
    date_history_db["provider"].calls.clear()
    second = date_history_db["service"].resume(
        first.run_id,
        HistoryRunActionRequest(
            apply=True,
            data_cutoff=CUTOFF,
            request_budget=5,
        ),
    )
    assert second.status == "PAUSED_BUDGET"
    assert second.resume_cursor == 4
    assert date_history_db["provider"].calls == targets[2:4]
    date_history_db["provider"].calls.clear()
    final = date_history_db["service"].resume(
        first.run_id,
        HistoryRunActionRequest(
            apply=True,
            data_cutoff=CUTOFF,
            request_budget=5,
        ),
    )
    assert final.status == "SUCCESS"
    assert final.resume_cursor == 5
    assert date_history_db["provider"].calls == targets[4:]


def test_33_resume_cursor_ignores_successful_dates_after_pending_work(
    date_history_db: dict[str, Any],
) -> None:
    targets = date_history_db["days"][-5:]
    date_history_db["service"].run(_request([targets[-1]]))
    date_history_db["provider"].calls.clear()
    result = date_history_db["service"].run(
        _request(targets, budget=4, batch_size=2)
    )
    assert result.status == "PAUSED_BUDGET"
    assert result.resume_cursor == 2
    assert date_history_db["provider"].calls == targets[:2]


def test_34_ten_date_run_requests_only_eight_pending_dates(
    date_history_db: dict[str, Any],
) -> None:
    targets = date_history_db["days"][-10:]
    date_history_db["service"].run(_request(targets[-2:]))
    date_history_db["provider"].calls.clear()
    result = date_history_db["service"].run(
        _request(targets, budget=8, batch_size=10)
    )
    assert result.status == "SUCCESS"
    assert result.request_count == 8
    assert date_history_db["provider"].calls == targets[:8]
    assert all(
        item.payload["skipped_existing_success"] is True
        for item in result.date_shards[-2:]
    )


def test_35_historical_universe_uses_listing_and_delisting_dates(
    date_history_db: dict[str, Any],
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO stock_universe (
                symbol, exchange, market, board, security_type,
                company_name, short_name, list_date, delist_date,
                listing_status, is_st, is_suspended, currency,
                price_limit_type, primary_source, source_record_ids_json,
                verification_status, source_differences_json,
                data_available_time, updated_at, universe_version
            )
            VALUES (
                '002898.SZ', 'SZ', 'CN_A', 'MAIN', 'STOCK',
                'HISTORICAL', 'HISTORICAL', '2017-09-12', '2026-07-01',
                'DELISTED', FALSE, FALSE, 'CNY', 'STANDARD', 'TEST', '[]',
                'SINGLE_SOURCE', '{}', ?, ?, 'date-test-v1'
            )
            """,
            [CUTOFF - timedelta(days=1)] * 2,
        )
    universe = date_history_db["repository"].active_universe_context(
        data_cutoff=CUTOFF
    )
    assert "002898.SZ" in universe
    assert "002898.SZ" in TradeDateHistoryBackfillService._expected_symbols(
        universe,
        date(2026, 7, 1),
    )
    assert "002898.SZ" not in (
        TradeDateHistoryBackfillService._expected_symbols(
            universe,
            date(2026, 7, 2),
        )
    )
