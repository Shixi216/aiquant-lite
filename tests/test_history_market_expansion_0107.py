from __future__ import annotations

import inspect
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from pydantic import ValidationError

from config.settings import settings
from database.db import get_connection, initialize_database
from database.migrations.v0107_historical_market_expansion import (
    MIGRATION_ID,
    apply_migration,
)
from data_hub.providers.full_market import ProviderBatchResult
from data_hub.repositories.full_market import FullMarketRepository
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.full_market import (
    AnalysisMode,
    CandidateEnrichmentRequest,
    DataExpansionRequest,
    ExpansionType,
)
from data_hub.schemas.history import (
    AdjustmentType,
    HistoryBackfillRequest,
    HistoryRunActionRequest,
    SelectionStrategy,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.candidate_enrichment_service import (
    CandidateEnrichmentService,
)
from data_hub.services.data_expansion_service import DataExpansionService
from data_hub.services.daily_data_update_service import DailyDataUpdateService
from data_hub.services.full_market_common import stable_hash
from data_hub.services.history_backfill_service import HistoryBackfillService
from data_hub.services.history_coverage_service import HistoryCoverageService
from data_hub.services.history_provider_service import (
    HistoryProviderVerificationService,
    TradingCalendarService,
)
from scripts.history_cli import build_parser
from trading.decision_support.orchestrator.service import _generate_strategy
from trading.research.capital_flow.policy import policy_for as capital_policy
from trading.research.policy_news.policy import policy_for as policy_news_policy
from trading.research.sentiment.policy import policy_for as sentiment_policy
from trading.schemas import DecisionPacket


TZ = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime(2026, 7, 29, 20, tzinfo=TZ)


class TickClock:
    def __init__(self) -> None:
        self.value = CUTOFF

    def __call__(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


class FakeDaily:
    def __init__(
        self,
        *,
        source: str = "FakeBao",
        fail: bool = False,
        close_offset: float = 0,
        unknown_adjustment: bool = False,
        include_future: bool = False,
    ) -> None:
        self.source = source
        self.fail = fail
        self.close_offset = close_offset
        self.unknown_adjustment = unknown_adjustment
        self.include_future = include_future
        self.calls = 0

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjustment_type: str = "RAW",
    ) -> list[MarketRecord]:
        self.calls += 1
        if self.fail:
            raise ConnectionError(f"{self.source} unavailable")
        start = datetime.strptime(start_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()
        dates: list[date] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
        if self.include_future:
            dates.append(end + timedelta(days=1))
        records: list[MarketRecord] = []
        for trade_date in dates:
            payload = {
                "symbol": symbol,
                "trade_date": trade_date.strftime("%Y%m%d"),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5 + self.close_offset,
                "volume": 1_000.0,
                "amount": 10_000.0,
                "volume_unit": "SHARES",
                "amount_unit": "CNY",
                "provider_adjustment": adjustment_type,
            }
            if not self.unknown_adjustment:
                payload["adjustment_type"] = adjustment_type
            digest = stable_hash(
                {
                    "source": self.source,
                    "symbol": symbol,
                    "payload": payload,
                }
            )
            records.append(
                MarketRecord(
                    record_id=f"raw_test_{digest[:32]}",
                    symbol=symbol,
                    data_type=DataType.DAILY_BAR,
                    event_time=datetime.combine(
                        trade_date,
                        time(hour=15),
                        tzinfo=TZ,
                    ),
                    fetched_at=CUTOFF,
                    source_name=self.source,
                    source_level=SourceLevel.STRUCTURED,
                    verified=False,
                    content_hash=digest,
                    data=payload,
                )
            )
        return records


class EmptyAnnouncements:
    def fetch_announcements(self, trade_date: date) -> ProviderBatchResult:
        del trade_date
        return ProviderBatchResult(
            provider="FakeBatch",
            capability="BATCH_ANNOUNCEMENTS_BY_DATE",
            frame=pd.DataFrame(),
            request_count=1,
            metadata={"window": "one day"},
        )


class FailedNews:
    def fetch_global_finance_news(self) -> ProviderBatchResult:
        raise ConnectionError("bounded news failure")


def _calendar_rows(start: date, end: date) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current = start
    while current <= end:
        rows.append(
            {
                "calendar_date": current.isoformat(),
                "is_trading_day": "1" if current.weekday() < 5 else "0",
            }
        )
        current += timedelta(days=1)
    return rows


def _seed_universe() -> None:
    stocks = [
        ("600000.SH", "SH", "MAIN", date(1999, 11, 10), "ACTIVE"),
        ("000001.SZ", "SZ", "MAIN", date(1991, 4, 3), "ACTIVE"),
        ("300001.SZ", "SZ", "CHINEXT", date(2009, 10, 30), "ACTIVE"),
        (
            "301999.SZ",
            "SZ",
            "CHINEXT",
            CUTOFF.date() - timedelta(days=10),
            "ACTIVE",
        ),
        ("600999.SH", "SH", "MAIN", date(2000, 1, 1), "DELISTED"),
    ]
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO stock_universe_versions
            VALUES (
                'test-v1', 'test-universe-hash', ?, ?, ?, 5, 4,
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
                ?, ?, 'CN_A', ?, 'STOCK', ?, ?, ?, NULL, ?,
                FALSE, FALSE, 'CNY', 'STANDARD', 'TEST', '[]',
                'SINGLE_SOURCE', '{}', ?, ?, 'test-v1'
            )
            """,
            [
                [
                    symbol,
                    exchange,
                    board,
                    symbol,
                    symbol,
                    list_date,
                    status,
                    CUTOFF - timedelta(days=1),
                    CUTOFF - timedelta(days=1),
                ]
                for symbol, exchange, board, list_date, status in stocks
            ],
        )


@pytest.fixture
def history_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    path = tmp_path / "history-0107.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    monkeypatch.setattr(settings, "tushare_token", "")
    initialize_database()
    _seed_universe()
    repository = HistoryRepository()
    clock = TickClock()
    calendar = TradingCalendarService(
        repository=repository,
        calendar_fetcher=_calendar_rows,
        clock=clock,
    )
    provider = FakeDaily()
    service = HistoryBackfillService(
        repository=repository,
        calendar=calendar,
        providers={"BAOSTOCK": provider, "AKSHARE": provider},
        clock=clock,
    )
    return {
        "path": path,
        "repository": repository,
        "calendar": calendar,
        "provider": provider,
        "service": service,
        "clock": clock,
    }


def _request(
    *,
    symbols: list[str] | None = None,
    dry_run: bool = False,
    budget: int = 10,
    adjustment: AdjustmentType = AdjustmentType.RAW,
) -> HistoryBackfillRequest:
    return HistoryBackfillRequest(
        dry_run=dry_run,
        provider="BAOSTOCK",
        fallback_providers=["AKSHARE"],
        symbols=symbols or ["600000.SH"],
        target_trading_days=60,
        batch_size=max(1, len(symbols or ["600000.SH"])),
        concurrency=1,
        request_budget=budget,
        max_retries=0,
        data_cutoff=CUTOFF,
        adjustment_type=adjustment,
        selection_strategy=SelectionStrategy.EXPLICIT,
        max_symbols=max(1, len(symbols or ["600000.SH"])),
        minimum_free_bytes=0,
    )


def _counts() -> tuple[int, int, int]:
    with get_connection() as connection:
        return tuple(
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            for table in (
                "data_records",
                "canonical_historical_bars",
                "canonical_market_records",
            )
        )


def test_01_applied_0107_checksum_is_immutable(history_db: dict[str, object]) -> None:
    del history_db
    with get_connection() as connection:
        assert apply_migration(connection) is False
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = ?",
            [MIGRATION_ID],
        ).fetchone()[0] == 1


def test_02_calendar_write_and_range_query(history_db: dict[str, object]) -> None:
    calendar = history_db["calendar"]
    assert isinstance(calendar, TradingCalendarService)
    days, requests, inserted = calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    assert len(days) == 60 and requests == 1 and inserted > 60


def test_03_calendar_write_is_idempotent(history_db: dict[str, object]) -> None:
    calendar = history_db["calendar"]
    assert isinstance(calendar, TradingCalendarService)
    calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    _, _, inserted = calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    assert inserted == 0


def test_04_calendar_rejects_future_dates(history_db: dict[str, object]) -> None:
    calendar = history_db["calendar"]
    assert isinstance(calendar, TradingCalendarService)
    with pytest.raises(ValueError):
        calendar.fetch(
            start_date=CUTOFF.date(),
            end_date=CUTOFF.date() + timedelta(days=1),
            data_cutoff=CUTOFF,
            persist=False,
        )


def test_05_missing_calendar_fails_safely(history_db: dict[str, object]) -> None:
    repository = history_db["repository"]
    assert isinstance(repository, HistoryRepository)

    def fail(_: date, __: date) -> list[dict[str, str]]:
        raise ConnectionError("calendar unavailable")

    calendar = TradingCalendarService(
        repository=repository,
        calendar_fetcher=fail,
    )
    with pytest.raises(RuntimeError):
        calendar.ensure_recent(
            data_cutoff=CUTOFF,
            target_trading_days=60,
            persist=False,
        )


def test_06_dry_run_does_not_write_database(history_db: dict[str, object]) -> None:
    service = history_db["service"]
    assert isinstance(service, HistoryBackfillService)
    before = _counts()
    result = service.run(_request(dry_run=True))
    assert result.mode == "DRY_RUN"
    assert _counts() == before


def test_07_apply_writes_raw_and_canonical_history(
    history_db: dict[str, object],
) -> None:
    service = history_db["service"]
    assert isinstance(service, HistoryBackfillService)
    result = service.run(_request())
    raw, bars, _ = _counts()
    assert result.successful_symbol_count == 1
    assert raw == 60 and bars == 60


def test_08_apply_does_not_write_legacy_canonical_market(
    history_db: dict[str, object],
) -> None:
    service = history_db["service"]
    assert isinstance(service, HistoryBackfillService)
    before = _counts()[2]
    service.run(_request())
    assert _counts()[2] == before


def test_09_repeat_apply_is_idempotent(history_db: dict[str, object]) -> None:
    service = history_db["service"]
    assert isinstance(service, HistoryBackfillService)
    service.run(_request())
    first = _counts()
    second = service.run(_request())
    assert _counts() == first
    assert second.persisted_raw_count == 0
    assert second.persisted_canonical_count == 0


def test_10_request_budget_pauses_bounded_run(
    history_db: dict[str, object],
) -> None:
    calendar = history_db["calendar"]
    service = history_db["service"]
    assert isinstance(calendar, TradingCalendarService)
    assert isinstance(service, HistoryBackfillService)
    calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    result = service.run(
        _request(symbols=["600000.SH", "000001.SZ"], budget=1)
    )
    assert result.request_count == 1
    assert result.completed_symbol_count == 1
    assert result.status.value == "PAUSED_BUDGET"


def test_11_concurrency_is_hard_limited() -> None:
    with pytest.raises(ValidationError):
        HistoryBackfillRequest.model_validate(
            {**_request().model_dump(), "concurrency": 4}
        )


def test_12_one_symbol_failure_does_not_break_others(
    history_db: dict[str, object],
) -> None:
    repository = history_db["repository"]
    calendar = history_db["calendar"]
    clock = history_db["clock"]
    assert isinstance(repository, HistoryRepository)
    assert isinstance(calendar, TradingCalendarService)
    service = HistoryBackfillService(
        repository=repository,
        calendar=calendar,
        providers={
            "BAOSTOCK": FakeDaily(fail=True),
            "AKSHARE": FakeDaily(source="FakeAK"),
        },
        clock=clock,
    )
    result = service.run(
        _request(symbols=["600000.SH", "000001.SZ"], budget=5)
    )
    assert result.successful_symbol_count == 2
    assert {item.provider_used for item in result.items} == {"AKSHARE"}


def test_13_baostock_failure_falls_back_to_akshare(
    history_db: dict[str, object],
) -> None:
    repository = history_db["repository"]
    calendar = history_db["calendar"]
    clock = history_db["clock"]
    assert isinstance(repository, HistoryRepository)
    assert isinstance(calendar, TradingCalendarService)
    service = HistoryBackfillService(
        repository=repository,
        calendar=calendar,
        providers={
            "BAOSTOCK": FakeDaily(fail=True),
            "AKSHARE": FakeDaily(source="FakeAK"),
        },
        clock=clock,
    )
    result = service.run(_request(budget=3))
    assert result.items[0].provider_used == "AKSHARE"
    assert result.request_count == 3


def test_14_no_tushare_token_is_explicitly_skipped(
    history_db: dict[str, object],
) -> None:
    repository = history_db["repository"]
    calendar = history_db["calendar"]
    assert isinstance(repository, HistoryRepository)
    assert isinstance(calendar, TradingCalendarService)
    result = HistoryProviderVerificationService(
        repository=repository,
        calendar=calendar,
        akshare=FakeDaily(source="FakeAK"),
        baostock=FakeDaily(),
    ).verify(data_cutoff=CUTOFF)
    tushare = [item for item in result.results if item.provider == "Tushare"]
    assert tushare and {item.status for item in tushare} == {"SKIPPED_NO_TOKEN"}


def test_15_adjustment_types_are_separate_records(
    history_db: dict[str, object],
) -> None:
    service = history_db["service"]
    assert isinstance(service, HistoryBackfillService)
    service.run(_request(adjustment=AdjustmentType.RAW))
    service.run(
        _request(adjustment=AdjustmentType.FORWARD_ADJUSTED)
    )
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT adjustment_type, COUNT(*)
            FROM canonical_historical_bars
            GROUP BY adjustment_type
            """
        ).fetchall()
    assert dict(rows) == {"RAW": 60, "FORWARD_ADJUSTED": 60}


def test_16_unknown_adjustment_is_not_silently_accepted(
    history_db: dict[str, object],
) -> None:
    repository = history_db["repository"]
    calendar = history_db["calendar"]
    clock = history_db["clock"]
    assert isinstance(repository, HistoryRepository)
    assert isinstance(calendar, TradingCalendarService)
    result = HistoryBackfillService(
        repository=repository,
        calendar=calendar,
        providers={"BAOSTOCK": FakeDaily(unknown_adjustment=True)},
        clock=clock,
    ).run(_request())
    assert result.failed_symbol_count == 1
    assert "ADJUSTMENT_UNKNOWN" in (result.items[0].error_message or "")


def test_17_future_provider_rows_are_filtered(
    history_db: dict[str, object],
) -> None:
    repository = history_db["repository"]
    calendar = history_db["calendar"]
    clock = history_db["clock"]
    assert isinstance(repository, HistoryRepository)
    assert isinstance(calendar, TradingCalendarService)
    HistoryBackfillService(
        repository=repository,
        calendar=calendar,
        providers={"BAOSTOCK": FakeDaily(include_future=True)},
        clock=clock,
    ).run(_request())
    with get_connection() as connection:
        latest = connection.execute(
            "SELECT MAX(trade_date) FROM canonical_historical_bars"
        ).fetchone()[0]
    assert latest <= CUTOFF.date()


def test_18_data_available_time_respects_market_close(
    history_db: dict[str, object],
) -> None:
    service = history_db["service"]
    assert isinstance(service, HistoryBackfillService)
    service.run(_request())
    with get_connection() as connection:
        earliest_hour = connection.execute(
            """
            SELECT MIN(EXTRACT(hour FROM data_available_time))
            FROM canonical_historical_bars
            """
        ).fetchone()[0]
    assert earliest_hour == 16


def test_19_conflicting_same_adjustment_is_isolated(
    history_db: dict[str, object],
) -> None:
    repository = history_db["repository"]
    calendar = history_db["calendar"]
    clock = history_db["clock"]
    assert isinstance(repository, HistoryRepository)
    assert isinstance(calendar, TradingCalendarService)
    first = HistoryBackfillService(
        repository=repository,
        calendar=calendar,
        providers={"BAOSTOCK": FakeDaily(close_offset=0)},
        clock=clock,
    )
    second = HistoryBackfillService(
        repository=repository,
        calendar=calendar,
        providers={"BAOSTOCK": FakeDaily(close_offset=1)},
        clock=clock,
    )
    first.run(_request())
    second.run(_request())
    with get_connection() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM canonical_historical_bars
            WHERE verification_status = 'CONFLICT'
            """
        ).fetchone()[0] == 120


def test_20_new_stock_is_not_in_60d_denominator(
    history_db: dict[str, object],
) -> None:
    calendar = history_db["calendar"]
    assert isinstance(calendar, TradingCalendarService)
    calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    report = HistoryCoverageService().generate(
        data_cutoff=CUTOFF + timedelta(hours=1)
    )
    assert report.eligibility.newly_listed_60d_count == 1
    assert report.eligibility.eligible_60d_count == 3


def test_21_long_suspension_is_counted_separately(
    history_db: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = history_db["calendar"]
    assert isinstance(calendar, TradingCalendarService)
    monkeypatch.setattr(settings, "history_long_suspension_min_snapshots", 2)
    calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    with get_connection() as connection:
        for index in range(2):
            snapshot_time = CUTOFF - timedelta(minutes=index + 1)
            connection.execute(
                """
                INSERT INTO market_snapshot_runs VALUES (
                    ?, 'APPLY', 'TEST', ?, 4, 4, 4, 0, 1,
                    'COMPLETE', ?, ?, ?, ?, 1, 0, NULL, NULL, NULL
                )
                """,
                [
                    f"susp-{index}",
                    snapshot_time,
                    snapshot_time,
                    snapshot_time,
                    snapshot_time,
                    f"susp-hash-{index}",
                ],
            )
            connection.execute(
                """
                INSERT INTO market_snapshot_items VALUES (
                    ?, '300001.SZ', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    ?, 'TEST', 'SUSPENDED', TRUE, FALSE, NULL, '{}'
                )
                """,
                [f"susp-{index}", snapshot_time],
            )
    report = HistoryCoverageService().generate(
        data_cutoff=CUTOFF + timedelta(hours=1)
    )
    assert report.eligibility.long_suspended_count == 1
    assert report.eligibility.eligible_60d_count == 2


def test_22_failed_collection_count_uses_latest_attempt(
    history_db: dict[str, object],
) -> None:
    repository = history_db["repository"]
    calendar = history_db["calendar"]
    clock = history_db["clock"]
    assert isinstance(repository, HistoryRepository)
    assert isinstance(calendar, TradingCalendarService)
    HistoryBackfillService(
        repository=repository,
        calendar=calendar,
        providers={"BAOSTOCK": FakeDaily(fail=True)},
        clock=clock,
    ).run(_request())
    report = HistoryCoverageService().generate(
        data_cutoff=CUTOFF + timedelta(hours=1)
    )
    assert report.eligibility.failed_collection_count == 1


def test_23_empty_announcement_batch_is_success_not_failure(
    history_db: dict[str, object],
) -> None:
    del history_db
    result = DataExpansionService(
        repository=FullMarketRepository(),
        batch_provider=EmptyAnnouncements(),
        clock=TickClock(),
    ).run(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.ANNOUNCEMENTS,
            trade_date=CUTOFF.date(),
        )
    )
    with get_connection() as connection:
        status = connection.execute(
            "SELECT status FROM collection_window_audits"
        ).fetchone()[0]
    assert result.failed_count == 0 and status == "SUCCESS_EMPTY"


def test_24_news_failure_is_a_failed_collection_batch(
    history_db: dict[str, object],
) -> None:
    del history_db
    result = DataExpansionService(
        repository=FullMarketRepository(),
        batch_provider=FailedNews(),
        clock=TickClock(),
    ).run(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.FINANCE_NEWS,
        )
    )
    with get_connection() as connection:
        status = connection.execute(
            "SELECT status FROM collection_window_audits"
        ).fetchone()[0]
    assert result.failed_count == 1 and status == "FAILED"


def test_25_screening_candidate_never_calls_history_network(
    history_db: dict[str, object],
) -> None:
    class NoCall:
        def run(self, _: object) -> None:
            raise AssertionError("SCREENING must remain local")

    result = CandidateEnrichmentService(
        repository=FullMarketRepository(),
        history_service=NoCall(),
        clock=TickClock(),
    ).enrich(
        CandidateEnrichmentRequest(
            analysis_mode=AnalysisMode.SCREENING,
            dry_run=True,
            provider="AUTO",
            request_budget=0,
            data_cutoff=CUTOFF,
            symbols=["600000.SH"],
        )
    )
    assert result.request_count == 0


def test_26_research_candidate_uses_persisted_history_layer(
    history_db: dict[str, object],
) -> None:
    calendar = history_db["calendar"]
    history = history_db["service"]
    assert isinstance(calendar, TradingCalendarService)
    assert isinstance(history, HistoryBackfillService)
    calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    result = CandidateEnrichmentService(
        repository=FullMarketRepository(),
        history_service=history,
        clock=TickClock(),
    ).enrich(
        CandidateEnrichmentRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="BAOSTOCK",
            request_budget=1,
            data_cutoff=CUTOFF,
            symbols=["600000.SH"],
            minimum_history_days=60,
        )
    )
    assert "DAILY_BARS" in result.items[0].fetched_data_types
    assert _counts()[1] == 60


def test_27_history_api_is_only_registered_in_data_hub(
    history_db: dict[str, object],
) -> None:
    del history_db
    from data_hub.api.app import app as data_hub_app
    from router.api.app import app as router_app

    data_paths = data_hub_app.openapi()["paths"]
    router_paths = router_app.openapi()["paths"]
    assert "/v1/history-backfill/runs" in data_paths
    assert "/v1/history-backfill/runs" not in router_paths


def test_28_history_cli_writes_only_with_explicit_apply() -> None:
    parser = build_parser()
    planned = parser.parse_args(
        [
            "run_history_backfill",
            "--data-cutoff",
            CUTOFF.isoformat(),
            "--symbol",
            "600000.SH",
        ]
    )
    assert planned.apply is False


def test_29_history_cli_has_all_required_commands() -> None:
    source = inspect.getsource(build_parser)
    for command in (
        "verify_provider_capabilities",
        "plan_history_backfill",
        "run_history_backfill",
        "resume_history_backfill",
        "cancel_history_backfill",
        "generate_history_coverage_report",
    ):
        assert command in source


def test_30_resume_continues_from_pending_item(
    history_db: dict[str, object],
) -> None:
    calendar = history_db["calendar"]
    service = history_db["service"]
    assert isinstance(calendar, TradingCalendarService)
    assert isinstance(service, HistoryBackfillService)
    calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    first = service.run(
        _request(symbols=["600000.SH", "000001.SZ"], budget=1)
    )
    resumed = service.resume(
        first.run_id,
        HistoryRunActionRequest(
            apply=True,
            data_cutoff=CUTOFF,
            request_budget=1,
        ),
    )
    assert resumed.completed_symbol_count == 2
    assert resumed.status.value == "SUCCESS"


def test_31_cancel_dry_run_does_not_change_status(
    history_db: dict[str, object],
) -> None:
    calendar = history_db["calendar"]
    service = history_db["service"]
    assert isinstance(calendar, TradingCalendarService)
    assert isinstance(service, HistoryBackfillService)
    calendar.ensure_recent(
        data_cutoff=CUTOFF,
        target_trading_days=60,
        persist=True,
    )
    first = service.run(
        _request(symbols=["600000.SH", "000001.SZ"], budget=1)
    )
    detail = service.cancel(
        first.run_id,
        HistoryRunActionRequest(apply=False, data_cutoff=CUTOFF),
    )
    assert detail.run["status"] == "PAUSED_BUDGET"


def test_32_shard_transaction_rolls_back_on_audit_error(
    history_db: dict[str, object],
) -> None:
    repository = history_db["repository"]
    service = history_db["service"]
    assert isinstance(repository, HistoryRepository)
    assert isinstance(service, HistoryBackfillService)
    record = FakeDaily().get_daily_bars(
        "999999.SZ",
        "20260729",
        "20260729",
    )[0]
    bar = service._normalize_record(
        record,
        adjustment_type=AdjustmentType.RAW,
        generated_at=CUTOFF,
    )
    before = _counts()
    with pytest.raises(KeyError):
        repository.persist_batch(
            raw_records=[record],
            historical_bars=[bar],
            item_updates=[],
            request_audits=[{"audit_id": "invalid"}],
        )
    assert _counts() == before


def test_33_formal_weights_and_shadow_factors_are_unchanged() -> None:
    source = inspect.getsource(_generate_strategy)
    assert "technical.score * 0.6" in source
    assert "fundamental.score * 0.4" in source
    assert sentiment_policy(AnalysisMode.RESEARCH).shadow_mode is True
    assert policy_news_policy(AnalysisMode.RESEARCH).shadow_mode is True
    assert capital_policy(AnalysisMode.RESEARCH).shadow_mode is True


def test_34_decision_packet_immutability_is_unchanged() -> None:
    assert DecisionPacket.model_config.get("frozen") is True


def test_35_daily_update_uses_bounded_history_service() -> None:
    source = inspect.getsource(DailyDataUpdateService.run)
    assert "SKIPPED_NO_BATCH_PROVIDER" not in source
    assert "HistoryBackfillRequest" in source
    assert "request_budget=remaining" in source
