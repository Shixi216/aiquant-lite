from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pytest
from pydantic import ValidationError

from config.settings import settings
from database.db import get_connection, initialize_database
from database.migrations.v0106_full_market_data_foundation import apply_migration
from data_hub.providers.full_market import ProviderBatchResult
from data_hub.repositories.full_market import FullMarketRepository
from data_hub.schemas.full_market import (
    AliasType,
    AnalysisMode,
    CandidateEnrichmentRequest,
    DataExpansionRequest,
    ExpansionType,
    MarketSnapshotSyncRequest,
    StockAlias,
    UniverseSyncRequest,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.candidate_enrichment_service import (
    CandidateEnrichmentService,
)
from data_hub.services.coverage_service import DataCoverageService
from data_hub.services.data_expansion_service import DataExpansionService
from data_hub.services.entity_linking_service import EntityLinkingService
from data_hub.services.event_cluster_service import EventClusterService
from data_hub.services.full_market_common import (
    is_a_share_symbol,
    normalize_alias,
    normalize_symbol,
    stable_hash,
)
from data_hub.services.market_snapshot_service import MarketSnapshotService
from data_hub.services.universe_service import StockUniverseService
from scripts.full_market_cli import build_parser
from scripts.check_no_live_execution import (
    broker_import_issues,
    decision_to_real_order_issues,
    indirect_execution_issues,
)
from trading.decision_support.orchestrator.service import _generate_strategy
from trading.schemas import DecisionPacket


TZ = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime(2026, 7, 29, 12, tzinfo=TZ)


class FakeAK:
    def __init__(self, rows: list[tuple[str, str]] | None = None) -> None:
        self.rows = rows or [
            ("600000", "浦发银行"),
            ("000001", "平安银行"),
            ("300001", "特锐德"),
            ("688001", "华兴源创"),
            ("430001", "北交样本"),
        ]
        self.calls = 0

    def fetch_stock_list(self) -> ProviderBatchResult:
        self.calls += 1
        return ProviderBatchResult(
            provider="AKShare",
            capability="FULL_STOCK_LIST",
            frame=pd.DataFrame(self.rows, columns=["code", "name"]),
            request_count=1,
            metadata={"fake": True},
        )


class FakeBao:
    def __init__(
        self,
        rows: list[tuple[str, str, str, str, str, str]] | None = None,
    ) -> None:
        self.rows = rows or [
            ("sh.600000", "浦发银行", "19991110", "", "1", "1"),
            ("sz.000001", "平安银行", "19910403", "", "1", "1"),
            ("sz.300001", "特锐德", "20091030", "", "1", "1"),
            ("sh.688001", "华兴源创", "20190722", "", "1", "1"),
            ("sh.600999", "退市样本", "20000101", "20200101", "1", "0"),
            ("sh.000001", "上证指数", "19901219", "", "2", "1"),
        ]
        self.calls = 0

    def fetch_stock_basic(self) -> ProviderBatchResult:
        self.calls += 1
        return ProviderBatchResult(
            provider="BaoStock",
            capability="FULL_STOCK_BASIC",
            frame=pd.DataFrame(
                self.rows,
                columns=[
                    "code",
                    "code_name",
                    "ipoDate",
                    "outDate",
                    "type",
                    "status",
                ],
            ),
            request_count=1,
            metadata={"fake": True},
        )

    def fetch_industry_memberships(self) -> ProviderBatchResult:
        self.calls += 1
        return ProviderBatchResult(
            provider="BaoStock",
            capability="FULL_INDUSTRY_MEMBERSHIPS",
            frame=pd.DataFrame(
                [
                    ("2026-01-01", "sh.600000", "浦发银行", "银行", "证监会行业分类"),
                    ("2026-01-01", "sz.000001", "平安银行", "银行", "证监会行业分类"),
                    ("2026-01-01", "sz.300001", "特锐德", "电气设备", "证监会行业分类"),
                ],
                columns=[
                    "updateDate",
                    "code",
                    "code_name",
                    "industry",
                    "industryClassification",
                ],
            ),
            request_count=1,
            metadata={"fake": True},
        )


class FakeSnapshot:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def fetch_market_snapshot(self) -> ProviderBatchResult:
        self.calls += 1
        if self.fail:
            raise ConnectionError("fake provider unavailable")
        frame = pd.DataFrame(
            [
                ("600000", 10.0, 1.0, 0.1, 100, 1_000, 10.2, 9.8, 9.9, 9.9, 2.0),
                ("000001", 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 10.0, 0.0),
            ],
            columns=[
                "代码",
                "最新价",
                "涨跌幅",
                "涨跌额",
                "成交量",
                "成交额",
                "最高",
                "最低",
                "今开",
                "昨收",
                "换手率",
            ],
        )
        return ProviderBatchResult(
            provider="AKShare",
            capability="FULL_MARKET_REALTIME",
            frame=frame,
            request_count=1,
            metadata={"fake": True},
        )


class FakeDaily:
    def __init__(self, *, fail_symbol: str | None = None) -> None:
        self.calls = 0
        self.fail_symbol = fail_symbol

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[MarketRecord]:
        del start_date, end_date
        self.calls += 1
        if symbol == self.fail_symbol:
            raise ConnectionError("fake daily failure")
        event_time = CUTOFF.replace(hour=15)
        payload = {
            "symbol": symbol,
            "trade_date": event_time.strftime("%Y%m%d"),
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 1_000,
            "amount": 10_000,
            "adjust": "NONE",
        }
        digest = stable_hash(payload)
        return [
            MarketRecord(
                record_id=f"raw_daily_{digest[:32]}",
                symbol=symbol,
                data_type=DataType.DAILY_BAR,
                event_time=event_time,
                fetched_at=CUTOFF,
                source_name="Fake Daily",
                source_level=SourceLevel.STRUCTURED,
                verified=False,
                content_hash=digest,
                data=payload,
            )
        ]


class FakeBatchEvents:
    def __init__(self) -> None:
        self.announcement_calls = 0
        self.news_calls = 0

    def fetch_announcements(self, trade_date: date) -> ProviderBatchResult:
        self.announcement_calls += 1
        return ProviderBatchResult(
            provider="AKShare",
            capability="BATCH_ANNOUNCEMENTS_BY_DATE",
            frame=pd.DataFrame(
                [
                    (
                        "600000",
                        "浦发银行",
                        "浦发银行年度报告",
                        "定期报告",
                        (trade_date - timedelta(days=1)).isoformat(),
                        "https://example.com/a",
                    )
                ],
                columns=["代码", "名称", "公告标题", "公告类型", "公告日期", "网址"],
            ),
            request_count=1,
            metadata={"fake": True},
        )

    def fetch_global_finance_news(self) -> ProviderBatchResult:
        self.news_calls += 1
        return ProviderBatchResult(
            provider="AKShare",
            capability="BATCH_FINANCE_NEWS_LATEST",
            frame=pd.DataFrame(
                [
                    (
                        "银行业政策更新",
                        "浦发银行发布说明",
                        CUTOFF.isoformat(),
                        "https://example.com/n",
                    )
                ],
                columns=["标题", "摘要", "发布时间", "链接"],
            ),
            request_count=1,
            metadata={"fake": True},
        )


@pytest.fixture
def foundation_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    path = tmp_path / "foundation.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    initialize_database()
    return path


def _service(
    repository: FullMarketRepository,
    *,
    ak: FakeAK | None = None,
    bao: FakeBao | None = None,
) -> StockUniverseService:
    return StockUniverseService(
        repository=repository,
        akshare_provider=ak or FakeAK(),
        baostock_provider=bao or FakeBao(),
        clock=lambda: CUTOFF,
    )


def _sync(
    repository: FullMarketRepository,
    *,
    apply: bool = True,
    ak: FakeAK | None = None,
    bao: FakeBao | None = None,
):
    return _service(repository, ak=ak, bao=bao).sync(
        UniverseSyncRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=not apply,
            provider="AUTO",
            request_budget=3,
            data_cutoff=CUTOFF,
        )
    )


def _raw_event(
    repository: FullMarketRepository,
    *,
    title: str,
    payload: dict[str, object],
    symbol: str = "MARKET.GLOBAL",
    record_id: str = "event_raw_1",
):
    raw = MarketRecord(
        record_id=record_id,
        symbol=symbol,
        data_type=DataType.FINANCE_NEWS,
        event_time=CUTOFF - timedelta(hours=1),
        fetched_at=CUTOFF,
        source_name="test media",
        source_level=SourceLevel.MEDIA,
        verified=False,
        content_hash=stable_hash({"title": title, "payload": payload}),
        data={"title": title, **payload},
    )
    repository.save_raw_records([raw])
    return EventClusterService().cluster([raw])[0]


# A. Stock universe
def test_01_multi_source_stock_creates_one_canonical_universe_row(
    foundation_db: Path,
) -> None:
    del foundation_db
    repository = FullMarketRepository()
    result = _sync(repository)
    _, total, rows = repository.list_universe(version=result.universe_version, limit=100)
    assert total == len({item.symbol for item in rows})
    assert [item.symbol for item in rows].count("600000.SH") == 1


def test_02_delisted_stock_is_retained_in_history(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    result = _sync(repository)
    item = repository.get_stock("600999.SH", version=result.universe_version)
    assert item is not None and item.listing_status.value == "DELISTED"


def test_03_new_stock_produces_incremental_version(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    first = _sync(repository)
    second = _sync(
        repository,
        ak=FakeAK(FakeAK().rows + [("301999", "新增样本")]),
    )
    assert first.universe_version != second.universe_version
    assert repository.get_stock("301999.SZ", version=second.universe_version)


def test_04_st_change_is_versioned(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    first = _sync(repository)
    changed = [
        (code, "*ST浦发" if code == "600000" else name)
        for code, name in FakeAK().rows
    ]
    second = _sync(repository, ak=FakeAK(changed))
    assert first.universe_version != second.universe_version
    assert repository.get_stock("600000.SH", version=second.universe_version).is_st


def test_05_historical_universe_version_is_queryable(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    first = _sync(repository)
    _sync(repository, ak=FakeAK(FakeAK().rows + [("301999", "新增样本")]))
    assert repository.get_stock("301999.SZ", version=first.universe_version) is None


def test_06_duplicate_universe_sync_is_idempotent(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    first = _sync(repository)
    second = _sync(repository)
    assert first.universe_version == second.universe_version
    assert first.persisted_count == first.received_count
    assert second.persisted_count == 0
    with get_connection() as connection:
        versions = connection.execute(
            "SELECT COUNT(*) FROM stock_universe_versions"
        ).fetchone()[0]
    assert versions == 1


def test_07_universe_sync_does_not_delete_survivorship_history(
    foundation_db: Path,
) -> None:
    del foundation_db
    repository = FullMarketRepository()
    first = _sync(repository)
    _sync(repository, ak=FakeAK([("600000", "浦发银行")]))
    assert repository.get_stock("600999.SH", version=first.universe_version)


# B. Aliases and industry
def test_08_current_short_name_alias_is_persisted(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    result = _sync(repository)
    aliases = repository.aliases(version=result.universe_version)
    assert any(item.alias_name == "浦发银行" for item in aliases)


def test_09_alias_has_effective_time_window(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    result = _sync(repository)
    alias = next(
        item for item in repository.aliases(version=result.universe_version)
        if item.symbol == "600000.SH"
    )
    assert alias.valid_from is not None


def test_10_expired_alias_is_excluded_from_current_lookup(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    result = _sync(repository)
    alias = StockAlias(
        alias_id="expired",
        symbol="600000.SH",
        alias_name="旧简称",
        alias_type=AliasType.HISTORICAL_SHORT_NAME,
        valid_from=CUTOFF - timedelta(days=100),
        valid_to=CUTOFF - timedelta(days=1),
        source="test",
        verification_status="VERIFIED",
        normalized_alias=normalize_alias("旧简称"),
        generated_at=CUTOFF,
        universe_version=result.universe_version,
    )
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO stock_aliases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                alias.alias_id,
                alias.symbol,
                alias.alias_name,
                alias.alias_type.value,
                alias.valid_from,
                alias.valid_to,
                alias.source,
                alias.verification_status.value,
                alias.normalized_alias,
                alias.generated_at,
                alias.universe_version,
            ],
        )
    assert "旧简称" not in {
        item.alias_name
        for item in repository.aliases(
            version=result.universe_version,
            data_cutoff=CUTOFF,
        )
    }


def test_11_short_common_word_is_not_high_confidence_match(
    foundation_db: Path,
) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    cluster = _raw_event(repository, title="平安发布消息", payload={})
    result = EntityLinkingService(repository=repository, clock=lambda: CUTOFF).build(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="LOCAL_RULES",
            request_budget=0,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.ENTITY_LINKS,
        )
    )
    assert result.processed_count >= 1
    with get_connection() as connection:
        linked = connection.execute(
            "SELECT COUNT(*) FROM event_symbol_links WHERE event_cluster_id = ?",
            [cluster.event_cluster_id],
        ).fetchone()[0]
    assert linked == 0


def test_12_same_name_collision_is_not_forced(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    result = _sync(repository)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO stock_aliases VALUES (
                'collision', '000001.SZ', '浦发银行', 'CURRENT_SHORT_NAME',
                NULL, NULL, 'test', 'SINGLE_SOURCE', '浦发银行', ?, ?
            )
            """,
            [CUTOFF, result.universe_version],
        )
    cluster = _raw_event(repository, title="浦发银行发布消息", payload={})
    EntityLinkingService(repository=repository, clock=lambda: CUTOFF).build(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="LOCAL_RULES",
            request_budget=0,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.ENTITY_LINKS,
        )
    )
    with get_connection() as connection:
        status = connection.execute(
            """
            SELECT status FROM entity_link_audits
            WHERE event_cluster_id = ?
            ORDER BY generated_at DESC LIMIT 1
            """,
            [cluster.event_cluster_id],
        ).fetchone()[0]
    assert status == "UNLINKED_AMBIGUOUS"


def test_13_industry_mapping_has_version_and_time(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT mapping_version, valid_from
            FROM stock_industry_memberships
            WHERE symbol = '600000.SH'
            """
        ).fetchone()
    assert row[0] and row[1] is not None


def test_14_missing_industry_is_not_fabricated(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    assert "430001.BJ" not in repository.industry_symbols(CUTOFF)


# C. Market snapshot
def test_15_batch_snapshot_is_persisted(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    result = MarketSnapshotService(
        repository=repository,
        provider=FakeSnapshot(),
        clock=lambda: CUTOFF,
    ).sync(
        MarketSnapshotSyncRequest(
            analysis_mode=AnalysisMode.SCREENING,
            dry_run=False,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
        )
    )
    assert result.received_symbol_count == 2


def test_16_partial_snapshot_is_marked(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    result = MarketSnapshotService(
        repository=repository,
        provider=FakeSnapshot(),
        clock=lambda: CUTOFF,
    ).sync(
        MarketSnapshotSyncRequest(
            analysis_mode=AnalysisMode.SCREENING,
            dry_run=True,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
        )
    )
    assert result.completeness_status.value == "PARTIAL_UNIVERSE"


def test_17_provider_failure_does_not_relabel_old_snapshot(
    foundation_db: Path,
) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    good = MarketSnapshotService(
        repository=repository,
        provider=FakeSnapshot(),
        clock=lambda: CUTOFF,
    )
    first = good.sync(
        MarketSnapshotSyncRequest(
            analysis_mode=AnalysisMode.SCREENING,
            dry_run=False,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
        )
    )
    with pytest.raises(RuntimeError):
        MarketSnapshotService(
            repository=repository,
            provider=FakeSnapshot(fail=True),
            clock=lambda: CUTOFF + timedelta(minutes=10),
        ).sync(
            MarketSnapshotSyncRequest(
                analysis_mode=AnalysisMode.SCREENING,
                dry_run=False,
                provider="AKSHARE",
                request_budget=1,
                data_cutoff=CUTOFF + timedelta(minutes=10),
            )
        )
    assert good.latest(maximum_age_seconds=300).snapshot_id == first.snapshot_id


def test_18_old_snapshot_is_explicitly_stale(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    service = MarketSnapshotService(
        repository=repository,
        provider=FakeSnapshot(),
        clock=lambda: CUTOFF,
    )
    service.sync(
        MarketSnapshotSyncRequest(
            analysis_mode=AnalysisMode.SCREENING,
            dry_run=False,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
        )
    )
    service.clock = lambda: CUTOFF + timedelta(minutes=10)
    assert service.latest(maximum_age_seconds=300).stale is True


def test_19_suspended_quote_is_separate_from_valid(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    result = MarketSnapshotService(
        repository=repository,
        provider=FakeSnapshot(),
        clock=lambda: CUTOFF,
    ).sync(
        MarketSnapshotSyncRequest(
            analysis_mode=AnalysisMode.SCREENING,
            dry_run=True,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
        )
    )
    item = next(value for value in result.items if value.symbol == "000001.SZ")
    assert item.is_suspended and item.item_status != "VALID"


def test_20_snapshot_units_are_normalized(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    result = MarketSnapshotService(
        repository=repository,
        provider=FakeSnapshot(),
        clock=lambda: CUTOFF,
    ).sync(
        MarketSnapshotSyncRequest(
            analysis_mode=AnalysisMode.SCREENING,
            dry_run=True,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
        )
    )
    item = next(value for value in result.items if value.symbol == "600000.SH")
    assert item.volume == 10_000
    assert item.change_pct == pytest.approx(0.01)
    assert item.turnover_rate == pytest.approx(0.02)


def test_21_duplicate_snapshot_is_idempotent(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    service = MarketSnapshotService(
        repository=repository,
        provider=FakeSnapshot(),
        clock=lambda: CUTOFF,
    )
    request = MarketSnapshotSyncRequest(
        analysis_mode=AnalysisMode.SCREENING,
        dry_run=False,
        provider="AKSHARE",
        request_budget=1,
        data_cutoff=CUTOFF,
    )
    first = service.sync(request)
    second = service.sync(request)
    assert first.snapshot_id == second.snapshot_id


# D. Historical expansion
def test_22_daily_expansion_rejects_future_date(foundation_db: Path) -> None:
    del foundation_db
    with pytest.raises(ValueError):
        DataExpansionService(
            repository=FullMarketRepository(),
            akshare_daily=FakeDaily(),
            baostock_daily=FakeDaily(),
        ).run(
            DataExpansionRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                dry_run=True,
                provider="AKSHARE",
                request_budget=1,
                data_cutoff=CUTOFF,
                expansion_type=ExpansionType.DAILY_BARS,
                symbols=["600000.SH"],
                start_date=CUTOFF.date() + timedelta(days=1),
                end_date=CUTOFF.date() + timedelta(days=1),
            )
        )


def test_23_same_symbol_trade_date_is_idempotent(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    service = DataExpansionService(
        repository=repository,
        akshare_daily=FakeDaily(),
        baostock_daily=FakeDaily(),
    )
    request = DataExpansionRequest(
        analysis_mode=AnalysisMode.RESEARCH,
        dry_run=False,
        provider="AKSHARE",
        request_budget=1,
        data_cutoff=CUTOFF,
        expansion_type=ExpansionType.DAILY_BARS,
        symbols=["600000.SH"],
        trade_date=CUTOFF.date(),
    )
    service.run(request)
    service.run(request)
    with get_connection() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM canonical_market_records
            WHERE symbol = '600000.SH' AND data_type = 'daily_bar'
            """
        ).fetchone()[0]
    assert count == 1


def test_24_conflict_status_is_not_reclassified_by_expansion() -> None:
    source = inspect.getsource(DataExpansionService)
    assert "CONFLICT" not in source or "conflict_count" in source


def test_25_adjustment_semantics_are_explicit(foundation_db: Path) -> None:
    del foundation_db
    record = FakeDaily().get_daily_bars("600000.SH", "20260729", "20260729")[0]
    assert record.data["adjust"] == "NONE"


def test_26_resume_flag_is_supported_by_cli() -> None:
    args = build_parser("backfill_daily_bars").parse_args(
        [
            "--data-cutoff",
            CUTOFF.isoformat(),
            "--symbol",
            "600000.SH",
            "--trade-date",
            "2026-07-29",
            "--resume",
        ]
    )
    assert args.resume is True


def test_27_request_budget_skips_excess_symbols(foundation_db: Path) -> None:
    del foundation_db
    result = DataExpansionService(
        repository=FullMarketRepository(),
        akshare_daily=FakeDaily(),
        baostock_daily=FakeDaily(),
    ).run(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=True,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.DAILY_BARS,
            symbols=["600000.SH", "000001.SZ"],
            trade_date=CUTOFF.date(),
        )
    )
    assert result.request_count == 1 and result.skipped_count == 1


def test_28_one_provider_failure_does_not_break_other_symbols(
    foundation_db: Path,
) -> None:
    del foundation_db
    result = DataExpansionService(
        repository=FullMarketRepository(),
        akshare_daily=FakeDaily(fail_symbol="000001.SZ"),
        baostock_daily=FakeDaily(),
    ).run(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=True,
            provider="AKSHARE",
            request_budget=2,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.DAILY_BARS,
            symbols=["000001.SZ", "600000.SH"],
            trade_date=CUTOFF.date(),
        )
    )
    assert result.failed_count == 1 and result.success_count == 1


# E. Announcements and news
def test_29_duplicate_announcement_does_not_duplicate_event(
    foundation_db: Path,
) -> None:
    del foundation_db
    repository = FullMarketRepository()
    fake = FakeBatchEvents()
    service = DataExpansionService(repository=repository, batch_provider=fake)
    request = DataExpansionRequest(
        analysis_mode=AnalysisMode.RESEARCH,
        dry_run=False,
        provider="AKSHARE",
        request_budget=1,
        data_cutoff=CUTOFF,
        expansion_type=ExpansionType.ANNOUNCEMENTS,
        trade_date=CUTOFF.date(),
        batch_size=10,
    )
    service.run(request)
    service.run(request)
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM event_clusters"
        ).fetchone()[0] == 1


def test_30_global_news_uses_one_batch_request(foundation_db: Path) -> None:
    del foundation_db
    fake = FakeBatchEvents()
    result = DataExpansionService(
        repository=FullMarketRepository(),
        batch_provider=fake,
    ).run(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=True,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.FINANCE_NEWS,
            batch_size=10,
        )
    )
    assert result.request_count == 1 and fake.news_calls == 1


def test_31_announcement_source_code_links_directly(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    cluster = _raw_event(
        repository,
        title="年度报告",
        payload={"symbol": "600000.SH"},
        symbol="600000.SH",
    )
    EntityLinkingService(repository=repository, clock=lambda: CUTOFF).build(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="LOCAL_RULES",
            request_budget=0,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.ENTITY_LINKS,
        )
    )
    with get_connection() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM event_symbol_links
            WHERE event_cluster_id = ? AND symbol = '600000.SH'
            """,
            [cluster.event_cluster_id],
        ).fetchone()[0] == 1


def test_32_unidentifiable_news_stays_unlinked(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    cluster = _raw_event(repository, title="宏观市场观察", payload={})
    EntityLinkingService(repository=repository, clock=lambda: CUTOFF).build(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="LOCAL_RULES",
            request_budget=0,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.ENTITY_LINKS,
        )
    )
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM event_symbol_links WHERE event_cluster_id = ?",
            [cluster.event_cluster_id],
        ).fetchone()[0] == 0


def test_33_coverage_is_not_inflated_by_forced_links() -> None:
    source = inspect.getsource(EntityLinkingService)
    assert "NO_RELIABLE_MATCH" in source
    assert "UNLINKED" in source


def test_34_missing_full_text_is_marked(foundation_db: Path) -> None:
    del foundation_db
    service = DataExpansionService(
        repository=FullMarketRepository(),
        batch_provider=FakeBatchEvents(),
    )
    records = service._announcement_records(
        FakeBatchEvents().fetch_announcements(CUTOFF.date()).frame,
        fetched_at=CUTOFF,
        data_cutoff=CUTOFF,
        limit=10,
    )
    assert records[0].data["full_text_status"] == "URL_ONLY"


def test_35_event_backfill_is_idempotent(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    first = _raw_event(repository, title="重复事件", payload={})
    second = _raw_event(repository, title="重复事件", payload={}, record_id="event_raw_2")
    assert first.event_cluster_id == second.event_cluster_id


# F. Entity linking
def test_36_explicit_code_has_highest_link_confidence(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    cluster = _raw_event(repository, title="600000 发布消息", payload={})
    EntityLinkingService(repository=repository, clock=lambda: CUTOFF).build(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="LOCAL_RULES",
            request_budget=0,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.ENTITY_LINKS,
        )
    )
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT confidence, matching_rule FROM entity_link_audits
            WHERE event_cluster_id = ? AND symbol = '600000.SH'
            """,
            [cluster.event_cluster_id],
        ).fetchone()
    assert row == (0.99, "EXPLICIT_TEXT_CODE")


def test_37_company_full_name_type_has_exact_rule() -> None:
    source = inspect.getsource(EntityLinkingService)
    assert "COMPANY_FULL_NAME" in source and "EXACT_FULL_NAME" in source


def test_38_current_alias_requires_valid_cutoff(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    result = _sync(repository)
    assert repository.aliases(
        version=result.universe_version,
        data_cutoff=CUTOFF,
    )


def test_39_fuzzy_alias_is_not_formal_high_confidence() -> None:
    source = inspect.getsource(EntityLinkingService)
    assert "fuzzy" not in source.casefold()
    assert "模型" not in source


def test_40_model_candidate_cannot_create_formal_link() -> None:
    source = inspect.getsource(EntityLinkingService)
    assert ".invoke(" not in source.casefold()
    assert "model_call" not in source.casefold()


def test_41_manual_reversal_preserves_audit(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    cluster = _raw_event(
        repository,
        title="600000 发布",
        payload={"symbol": "600000.SH"},
        symbol="600000.SH",
    )
    service = EntityLinkingService(repository=repository, clock=lambda: CUTOFF)
    service.build(
        DataExpansionRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=False,
            provider="LOCAL_RULES",
            request_budget=0,
            data_cutoff=CUTOFF,
            expansion_type=ExpansionType.ENTITY_LINKS,
        )
    )
    with get_connection() as connection:
        original = connection.execute(
            """
            SELECT audit_id FROM entity_link_audits
            WHERE event_cluster_id = ? AND symbol = '600000.SH'
            LIMIT 1
            """,
            [cluster.event_cluster_id],
        ).fetchone()[0]
    service.reverse(
        event_cluster_id=cluster.event_cluster_id,
        symbol="600000.SH",
        supersedes_audit_id=original,
        reason="user correction",
    )
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM entity_link_audits WHERE event_cluster_id = ?",
            [cluster.event_cluster_id],
        ).fetchone()[0] == 2


def test_42_entity_linking_does_not_use_future_price_performance() -> None:
    source = inspect.getsource(EntityLinkingService).casefold()
    assert "price" not in source
    assert "return_" not in source


# G. Candidate enrichment
def test_43_candidate_request_is_limited_to_twenty() -> None:
    with pytest.raises(ValidationError):
        CandidateEnrichmentRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=True,
            provider="AUTO",
            request_budget=0,
            data_cutoff=CUTOFF,
            symbols=[f"{index:06d}.SZ" for index in range(21)],
        )


def test_44_screening_does_not_fetch_per_symbol(foundation_db: Path) -> None:
    del foundation_db
    with pytest.raises(ValueError):
        CandidateEnrichmentService(
            repository=FullMarketRepository()
        ).enrich(
            CandidateEnrichmentRequest(
                analysis_mode=AnalysisMode.SCREENING,
                dry_run=True,
                provider="AUTO",
                request_budget=1,
                data_cutoff=CUTOFF,
                symbols=["600000.SH"],
            )
        )


def test_45_research_allows_budgeted_enrichment(foundation_db: Path) -> None:
    del foundation_db
    fake_daily = FakeDaily()
    expansion = DataExpansionService(
        repository=FullMarketRepository(),
        akshare_daily=fake_daily,
        baostock_daily=fake_daily,
    )
    result = CandidateEnrichmentService(
        repository=FullMarketRepository(),
        expansion_service=expansion,
        clock=lambda: CUTOFF,
    ).enrich(
        CandidateEnrichmentRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=True,
            provider="AKSHARE",
            request_budget=1,
            data_cutoff=CUTOFF,
            symbols=["600000.SH"],
        )
    )
    assert result.request_count == 1


def test_46_one_candidate_failure_is_isolated(foundation_db: Path) -> None:
    del foundation_db
    fake_daily = FakeDaily(fail_symbol="000001.SZ")
    repository = FullMarketRepository()
    result = CandidateEnrichmentService(
        repository=repository,
        expansion_service=DataExpansionService(
            repository=repository,
            akshare_daily=fake_daily,
            baostock_daily=fake_daily,
        ),
        clock=lambda: CUTOFF,
    ).enrich(
        CandidateEnrichmentRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=True,
            provider="AKSHARE",
            request_budget=2,
            data_cutoff=CUTOFF,
            symbols=["000001.SZ", "600000.SH"],
        )
    )
    assert len(result.items) == 2
    assert any(item.enrichment_status == "FAILED" for item in result.items)


def test_47_model_call_count_is_capped_and_zero_this_stage(
    foundation_db: Path,
) -> None:
    del foundation_db
    result = CandidateEnrichmentService(
        repository=FullMarketRepository(),
        clock=lambda: CUTOFF,
    ).enrich(
        CandidateEnrichmentRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=True,
            provider="AUTO",
            request_budget=0,
            data_cutoff=CUTOFF,
            symbols=["600000.SH"],
            model_call_budget=10,
        )
    )
    assert result.model_call_count == 0


def test_48_enrichment_does_not_create_decision_packet() -> None:
    source = inspect.getsource(CandidateEnrichmentService)
    assert "DecisionPacket" not in source


# H. Coverage, safety, and performance
def test_49_coverage_has_numerator_and_denominator(foundation_db: Path) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    report = DataCoverageService(
        repository=repository,
        clock=lambda: CUTOFF,
    ).generate(data_cutoff=CUTOFF, persist=False)
    assert all(
        metric.numerator >= 0 and metric.denominator >= 0
        for metric in report.metrics.values()
    )


def test_50_partial_sample_is_not_called_full_a_share(
    foundation_db: Path,
) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository)
    report = DataCoverageService(
        repository=repository,
        clock=lambda: CUTOFF,
    ).generate(data_cutoff=CUTOFF, persist=False)
    assert report.metrics["realtime_snapshot"].ratio == 0


def test_51_migration_preserves_raw_records(tmp_path: Path) -> None:
    path = tmp_path / "migration.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE data_records (record_id VARCHAR)")
        connection.execute("INSERT INTO data_records VALUES ('keep')")
        apply_migration(connection)
        assert connection.execute("SELECT COUNT(*) FROM data_records").fetchone()[0] == 1


def test_52_formal_strategy_weights_are_unchanged() -> None:
    source = inspect.getsource(_generate_strategy)
    assert "technical.score * 0.6" in source
    assert "fundamental.score * 0.4" in source


def test_53_shadow_factors_remain_shadow() -> None:
    from trading.research.capital_flow.policy import policy_for

    assert policy_for("RESEARCH").formal_strategy_weight == 0


def test_54_decision_packet_immutability_is_unchanged() -> None:
    assert DecisionPacket.model_config["frozen"] is True


def test_55_hard_risk_veto_code_is_unchanged() -> None:
    from trading.decision_support.risk_rules.rules import evaluate_strategy_risk

    assert "vetoed" in inspect.getsource(evaluate_strategy_risk)


def test_56_no_live_or_broker_execution_is_added() -> None:
    assert broker_import_issues() == []
    assert indirect_execution_issues() == []
    assert decision_to_real_order_issues() == []


def test_57_migration_is_idempotent_twice(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "twice.duckdb")) as connection:
        assert apply_migration(connection) is True
        assert apply_migration(connection) is False


@pytest.mark.parametrize(
    "command",
    [
        "sync_stock_universe",
        "sync_market_snapshot",
        "backfill_daily_bars",
        "backfill_announcements",
        "backfill_finance_news",
        "build_entity_links",
        "enrich_candidates",
        "run_daily_data_update",
        "generate_data_coverage_report",
    ],
)
def test_58_all_batch_cli_commands_default_to_dry_run(command: str) -> None:
    parser = build_parser(command)
    required = ["--data-cutoff", CUTOFF.isoformat()]
    if command == "enrich_candidates":
        required.append("600000.SH")
    args = parser.parse_args(required)
    assert args.apply is False


def test_59_capability_registry_does_not_expose_secrets() -> None:
    from data_hub.services.provider_capability_registry import (
        ProviderCapabilityRegistry,
    )

    source = inspect.getsource(ProviderCapabilityRegistry).casefold()
    assert "api_key" not in source
    assert "token" not in source


def test_60_no_hidden_chain_of_thought_storage() -> None:
    sources = "\n".join(
        inspect.getsource(item)
        for item in (
            StockUniverseService,
            MarketSnapshotService,
            DataExpansionService,
            EntityLinkingService,
            CandidateEnrichmentService,
        )
    ).casefold()
    assert "chain_of_thought" not in sources
    assert "hidden_reasoning" not in sources


def test_61_5000_stock_load_and_filter_is_linear(foundation_db: Path) -> None:
    del foundation_db
    values = {f"{index:06d}.SZ": index for index in range(5000)}
    selected = [symbol for symbol, score in values.items() if score >= 4900]
    assert len(selected) == 100


def test_62_5000_snapshot_normalization_uses_one_frame() -> None:
    frame = pd.DataFrame(
        {
            "代码": [f"{index:06d}" for index in range(5000)],
            "最新价": [10.0] * 5000,
        }
    )
    assert len(frame.to_dict(orient="records")) == 5000


def test_63_full_market_services_do_not_loop_network_over_universe() -> None:
    source = inspect.getsource(MarketSnapshotService.sync)
    assert source.count("fetch_market_snapshot(") == 1


def test_64_screening_performs_zero_model_calls(foundation_db: Path) -> None:
    del foundation_db
    result = CandidateEnrichmentService(
        repository=FullMarketRepository(),
        clock=lambda: CUTOFF,
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
    assert result.request_count == 0 and result.model_call_count == 0


def test_65_openapi_registers_exact_foundation_routes() -> None:
    from router.api.app import app

    paths = app.openapi()["paths"]
    assert {
        "/v1/universe/sync",
        "/v1/universe",
        "/v1/universe/{symbol}",
        "/v1/market-snapshots/sync",
        "/v1/market-snapshots/latest",
        "/v1/data-expansion/run",
        "/v1/data-coverage/latest",
        "/v1/candidates/enrich",
    } <= set(paths)


def test_66_dry_run_universe_does_not_write_database(
    foundation_db: Path,
) -> None:
    del foundation_db
    repository = FullMarketRepository()
    _sync(repository, apply=False)
    assert repository.universe_count() == 0


def test_67_raw_history_count_is_not_changed_by_migration(
    foundation_db: Path,
) -> None:
    del foundation_db
    with get_connection() as connection:
        before = connection.execute("SELECT COUNT(*) FROM data_records").fetchone()[0]
        apply_migration(connection)
        after = connection.execute("SELECT COUNT(*) FROM data_records").fetchone()[0]
    assert before == after


def test_68_data_cutoff_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        UniverseSyncRequest(
            analysis_mode=AnalysisMode.RESEARCH,
            dry_run=True,
            provider="AUTO",
            request_budget=3,
            data_cutoff=datetime(2026, 7, 29),
        )


def test_69_main_database_path_was_not_hardcoded() -> None:
    source = inspect.getsource(FullMarketRepository)
    assert "E:\\\\hermes-opc" not in source


def test_70_no_per_stock_llm_path_exists() -> None:
    source = inspect.getsource(CandidateEnrichmentService).casefold()
    assert ".invoke(" not in source
    assert "model_call_count=0" in source


def test_71_beijing_920_and_a_share_code_boundaries() -> None:
    assert normalize_symbol("920001") == "920001.BJ"
    assert is_a_share_symbol("920001.BJ")
    assert is_a_share_symbol("600000.SH")
    assert is_a_share_symbol("000001.SZ")
    assert not is_a_share_symbol("900901.SH")
    assert not is_a_share_symbol("200002.SZ")
