from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from pydantic import ValidationError

from config.settings import settings
from data_hub.repositories import (
    CanonicalFinancialRepository,
    CanonicalMarketRepository,
    FactorOutputRepository,
)
from data_hub.schemas.market import (
    DataType,
    MarketRecord,
    SourceLevel,
)
from data_hub.schemas.unified import (
    CanonicalFinancialRecord,
    CanonicalMarketRecord,
    VerificationStatus,
)
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from database.migrations.v0102_fundamental_point_in_time import (
    apply_migration,
)
from trading.decision_support.decision_packets import DecisionRepository
from trading.decision_support.orchestrator import DecisionService
from trading.research.fundamental.metrics import (
    calculate_fundamental_metrics,
)
from trading.research.fundamental.mode_router import (
    resolve_analysis_mode,
)
from trading.research.fundamental.models import (
    FundamentalRiskFlag,
)
from trading.research.fundamental.repository import (
    FundamentalRepository,
)
from trading.research.fundamental.service import (
    DecisionFundamentalService,
    FundamentalAnalysisService,
    ResearchFundamentalService,
    ScreeningFundamentalService,
)
from trading.schemas import (
    AnalysisMode,
    DecisionFromDataRequest,
    FundamentalDataStatus,
    FundamentalSnapshot,
    PortfolioState,
)


TZ = datetime.now().astimezone().tzinfo
NOW = datetime(2025, 5, 2, 12, 0, tzinfo=TZ)
CUTOFF = datetime(2025, 5, 1, 23, 0, tzinfo=TZ)


def _hash(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@pytest.fixture
def fundamental_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fundamental.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    initialize_database()
    return path


def _financial_payload(
    statement_type: str,
    *,
    report_period: str = "20241231",
    announcement_date: str | None = "20250430",
    end_type: str = "4",
    revenue: float = 1_200.0,
    net_income: float = 120.0,
    equity: float = 600.0,
    assets: float = 1_000.0,
    liabilities: float = 400.0,
    cashflow: float = 100.0,
    shares: float = 100.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "statement_type": statement_type,
        "report_period": report_period,
        "end_date": report_period,
        "end_type": end_type,
        "comp_type": "1",
        "update_flag": "1",
    }
    if announcement_date is not None:
        payload["f_ann_date"] = announcement_date
    if statement_type == "income":
        payload.update(
            {
                "revenue": revenue,
                "total_revenue": revenue,
                "n_income_attr_p": net_income,
            }
        )
    elif statement_type == "balancesheet":
        payload.update(
            {
                "total_hldr_eqy_exc_min_int": equity,
                "total_assets": assets,
                "total_liab": liabilities,
                "total_share": shares,
            }
        )
    elif statement_type == "cashflow":
        payload["n_cashflow_act"] = cashflow
    return payload


def _seed_financial(
    *,
    symbol: str = "600000.SH",
    report_period: str = "20241231",
    announcement_date: str | None = "20250430",
    status: VerificationStatus = VerificationStatus.SINGLE_SOURCE,
    end_type: str = "4",
    values: dict[str, float] | None = None,
) -> list[str]:
    values = values or {}
    raw_ids: list[str] = []
    for statement_type in ("income", "balancesheet", "cashflow"):
        payload = _financial_payload(
            statement_type,
            report_period=report_period,
            announcement_date=announcement_date,
            end_type=end_type,
            revenue=values.get("revenue", 1_200.0),
            net_income=values.get("net_income", 120.0),
            equity=values.get("equity", 600.0),
            assets=values.get("assets", 1_000.0),
            liabilities=values.get("liabilities", 400.0),
            cashflow=values.get("cashflow", 100.0),
            shares=values.get("shares", 100.0),
        )
        event_date = announcement_date or report_period
        event_time = datetime.strptime(
            event_date,
            "%Y%m%d",
        ).replace(hour=18, tzinfo=TZ)
        raw_id = (
            f"raw-{symbol}-{report_period}-{statement_type}-"
            f"{announcement_date or 'missing'}-{status.value}"
        )
        raw = MarketRecord(
            record_id=raw_id,
            symbol=symbol,
            data_type=DataType.FINANCIAL_STATEMENT,
            event_time=event_time,
            fetched_at=NOW,
            source_name=f"test-{statement_type}",
            source_level=SourceLevel.STRUCTURED,
            verified=status == VerificationStatus.VERIFIED,
            content_hash=_hash(payload),
            data=payload,
        )
        with get_connection() as connection:
            insert_market_record(connection, raw)
        canonical = CanonicalFinancialRecord(
            canonical_record_id="cfr_" + _hash(raw_id)[:32],
            symbol=symbol,
            data_type=f"financial_statement:{statement_type}",
            event_time=event_time,
            data_cutoff=NOW,
            generated_at=NOW,
            primary_source=f"test-{statement_type}",
            source_record_ids=[raw_id],
            verification_status=status,
            field_differences=(
                {"test": {"reason": "OUTSIDE_TOLERANCE"}}
                if status == VerificationStatus.CONFLICT
                else {}
            ),
            payload=payload,
            confidence=(
                0.0
                if status == VerificationStatus.CONFLICT
                else 0.9
                if status == VerificationStatus.VERIFIED
                else 0.5
            ),
            content_hash=_hash(
                {"raw_id": raw_id, "payload": payload}
            ),
            algorithm_version="test-v1",
        )
        CanonicalFinancialRepository().save(canonical)
        raw_ids.append(raw_id)
    return raw_ids


def _seed_market(
    *,
    symbol: str = "600000.SH",
    event_time: datetime,
    close: float,
) -> str:
    payload = {
        "trade_date": event_time.strftime("%Y%m%d"),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
    }
    raw_id = f"market-{symbol}-{event_time.date()}-{close}"
    raw = MarketRecord(
        record_id=raw_id,
        symbol=symbol,
        data_type=DataType.DAILY_BAR,
        event_time=event_time,
        fetched_at=NOW,
        source_name="market-test",
        source_level=SourceLevel.STRUCTURED,
        verified=True,
        content_hash=_hash(payload),
        data=payload,
    )
    with get_connection() as connection:
        insert_market_record(connection, raw)
    canonical = CanonicalMarketRecord(
        canonical_record_id="cmr_" + _hash(raw_id)[:32],
        symbol=symbol,
        data_type="daily_bar",
        event_time=event_time,
        data_cutoff=NOW,
        generated_at=NOW,
        primary_source="market-test",
        source_record_ids=[raw_id],
        verification_source_ids=[raw_id],
        verification_status=VerificationStatus.VERIFIED,
        payload=payload,
        confidence=0.95,
        content_hash=_hash({"canonical": payload}),
        algorithm_version="test-v1",
    )
    CanonicalMarketRepository().save(canonical)
    return raw_id


class CountingFetcher:
    def __init__(
        self,
        callback: Any | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls = 0
        self.callback = callback
        self.error = error

    def get_financial_statement(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.callback is not None:
            self.callback()
        return SimpleNamespace(records=[])


def _service(
    fetcher: CountingFetcher | None = None,
) -> FundamentalAnalysisService:
    actual_fetcher = fetcher or CountingFetcher()
    return FundamentalAnalysisService(
        fetcher_factory=lambda: actual_fetcher,
        now_factory=lambda: NOW,
    )


def _screen(service: FundamentalAnalysisService, symbol: str = "600000.SH"):
    return ScreeningFundamentalService(service).analyze(
        symbol=symbol,
        data_cutoff=CUTOFF,
    )


def _research(
    service: FundamentalAnalysisService,
    symbol: str = "600000.SH",
    **kwargs: Any,
):
    return ResearchFundamentalService(service).analyze(
        symbol=symbol,
        data_cutoff=CUTOFF,
        **kwargs,
    )


def _decision(
    service: FundamentalAnalysisService,
    symbol: str = "600000.SH",
    **kwargs: Any,
):
    return DecisionFundamentalService(service).analyze(
        symbol=symbol,
        data_cutoff=CUTOFF,
        **kwargs,
    )


def test_screening_missing_fundamentals_returns_candidate(
    fundamental_db: Path,
) -> None:
    result = _screen(_service())
    assert result.status == FundamentalDataStatus.UNAVAILABLE
    assert FundamentalRiskFlag.DATA_GAP in result.risk_flags


def test_screening_never_auto_fetches_each_symbol(
    fundamental_db: Path,
) -> None:
    fetcher = CountingFetcher()
    result = _screen(_service(fetcher))
    assert fetcher.calls == 0
    assert result.auto_fetch_result == "SKIPPED_MODE_RESTRICTION"


def test_screening_does_not_create_decision_packet(
    fundamental_db: Path,
) -> None:
    _screen(_service())
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM decision_packets"
        ).fetchone()[0] == 0


def test_screening_does_not_create_formal_factor(
    fundamental_db: Path,
) -> None:
    result = _screen(_service())
    assert result.factor_output is None
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM factor_outputs"
        ).fetchone()[0] == 0


def test_screening_has_no_trade_action_field(
    fundamental_db: Path,
) -> None:
    result = _screen(_service())
    assert "final_action" not in result.model_dump()


def test_screening_batch_uses_one_local_read_without_fetch(
    fundamental_db: Path,
) -> None:
    fetcher = CountingFetcher()
    summaries = ScreeningFundamentalService(
        _service(fetcher)
    ).analyze_many(
        symbols=[f"{index:06d}.SZ" for index in range(5000)],
        data_cutoff=CUTOFF,
    )
    assert len(summaries) == 5000
    assert fetcher.calls == 0
    assert all(
        summary.status == FundamentalDataStatus.UNAVAILABLE
        for summary in summaries
    )


def test_screening_batch_reads_existing_local_summary(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    summaries = ScreeningFundamentalService(
        _service()
    ).analyze_many(
        symbols=["600000.SH", "000001.SZ"],
        data_cutoff=CUTOFF,
    )
    assert summaries[0].used_report_period == "20241231"
    assert summaries[1].status == FundamentalDataStatus.UNAVAILABLE


def test_research_can_fetch_then_reads_canonical(
    fundamental_db: Path,
) -> None:
    fetcher = CountingFetcher(callback=_seed_financial)
    result = _research(_service(fetcher))
    assert fetcher.calls == 1
    assert result.canonical_record_ids
    assert result.auto_fetch_result == "SUCCESS_ATTEMPT_1"


def test_research_disclosure_missing_is_reference_only(
    fundamental_db: Path,
) -> None:
    _seed_financial(announcement_date=None)
    result = _research(_service(), auto_fetch=False)
    assert FundamentalRiskFlag.DISCLOSURE_TIME_MISSING in result.risk_flags
    assert result.factor_output is not None
    assert result.factor_output.shadow_mode is True


def test_research_factor_is_always_shadow(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    result = _research(_service())
    assert result.factor_output is not None
    assert result.factor_output.shadow_mode is True


def test_decision_forces_strict_point_in_time_schema() -> None:
    with pytest.raises(ValidationError, match="strict_point_in_time"):
        DecisionFromDataRequest(
            symbol="600000",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 1),
            portfolio=PortfolioState(cash=1, equity=1),
            strict_point_in_time=False,
        )


def test_decision_excludes_unknown_disclosure_time(
    fundamental_db: Path,
) -> None:
    _seed_financial(announcement_date=None)
    result = _decision(_service(), auto_fetch=False)
    assert result.status == FundamentalDataStatus.UNAVAILABLE
    assert not result.canonical_record_ids


def test_mode_wrappers_cannot_switch_policy(
    fundamental_db: Path,
) -> None:
    service = _service()
    assert _screen(service).analysis_mode == AnalysisMode.SCREENING
    assert _research(service).analysis_mode == AnalysisMode.RESEARCH
    assert _decision(service).analysis_mode == AnalysisMode.DECISION


def test_screening_wrapper_has_no_decision_service_dependency(
    fundamental_db: Path,
) -> None:
    wrapper = ScreeningFundamentalService(_service())
    assert "decision_service" not in vars(wrapper)


def test_wecom_fast_selection_defaults_to_screening() -> None:
    assert (
        resolve_analysis_mode("帮我筛选成交额放大的股票")
        == AnalysisMode.SCREENING
    )


def test_detailed_chat_request_routes_to_research() -> None:
    assert (
        resolve_analysis_mode("请对这只股票做详细分析")
        == AnalysisMode.RESEARCH
    )


def test_formal_decision_requires_explicit_flag() -> None:
    assert (
        resolve_analysis_mode(
            "生成记录",
            explicit_formal_decision=True,
        )
        == AnalysisMode.DECISION
    )


def test_disclosure_before_cutoff_is_available(
    fundamental_db: Path,
) -> None:
    _seed_financial(announcement_date="20250430")
    assert _decision(_service(), auto_fetch=False).canonical_record_ids


def test_disclosure_after_cutoff_is_not_available(
    fundamental_db: Path,
) -> None:
    _seed_financial(announcement_date="20250502")
    assert not _decision(
        _service(),
        auto_fetch=False,
    ).canonical_record_ids


def test_old_report_disclosed_late_does_not_leak(
    fundamental_db: Path,
) -> None:
    _seed_financial(
        report_period="20231231",
        announcement_date="20250502",
    )
    result = _decision(_service(), auto_fetch=False)
    assert result.used_report_period is None


def test_missing_disclosure_is_blocked_in_decision(
    fundamental_db: Path,
) -> None:
    _seed_financial(announcement_date=None)
    result = _decision(_service(), auto_fetch=False)
    assert result.verification_status is None


def test_historical_cutoff_never_fetches_current_data(
    fundamental_db: Path,
) -> None:
    fetcher = CountingFetcher()
    service = FundamentalAnalysisService(
        fetcher_factory=lambda: fetcher,
        now_factory=lambda: datetime(
            2026,
            5,
            1,
            tzinfo=TZ,
        ),
    )
    result = DecisionFundamentalService(service).analyze(
        symbol="600000.SH",
        data_cutoff=CUTOFF,
    )
    assert fetcher.calls == 0
    assert (
        FundamentalRiskFlag.HISTORICAL_DATA_GAP
        in result.risk_flags
    )


def test_historical_valuation_uses_only_prior_price(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    _seed_market(
        event_time=datetime(2025, 4, 29, 15, tzinfo=TZ),
        close=10,
    )
    _seed_market(
        event_time=datetime(2025, 5, 2, 15, tzinfo=TZ),
        close=99,
    )
    result = _decision(_service(), auto_fetch=False)
    assert result.metrics.pb == pytest.approx(10 * 100 / 600)


def test_pe_and_pb_use_cutoff_market_fact(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    _seed_financial(
        report_period="20231231",
        announcement_date="20240430",
        values={
            "revenue": 1_000,
            "net_income": 100,
            "equity": 500,
        },
    )
    _seed_market(
        event_time=datetime(2025, 4, 29, 15, tzinfo=TZ),
        close=12,
    )
    result = _decision(_service(), auto_fetch=False)
    assert result.metrics.pe_ttm == pytest.approx(10)
    assert result.metrics.pb == pytest.approx(2)


def test_fetched_at_cannot_replace_announcement_time(
    fundamental_db: Path,
) -> None:
    _seed_financial(announcement_date=None)
    rows = FundamentalRepository().list_at_cutoff(
        symbol="600000.SH",
        data_cutoff=CUTOFF,
        allow_missing_disclosure=False,
        include_conflicts=False,
    )
    assert rows == []


def test_automatic_canonical_financial_read(
    fundamental_db: Path,
) -> None:
    raw_ids = _seed_financial()
    result = _decision(_service(), auto_fetch=False)
    assert set(result.source_record_ids) == set(raw_ids)


def test_fetch_persists_raw_before_canonical_use(
    fundamental_db: Path,
) -> None:
    fetcher = CountingFetcher(callback=_seed_financial)
    result = _research(_service(fetcher))
    with get_connection() as connection:
        raw_count = connection.execute(
            "SELECT COUNT(*) FROM data_records"
        ).fetchone()[0]
    assert raw_count == 3
    assert len(result.canonical_record_ids) == 3


def test_uncanonicalized_raw_data_cannot_enter_decision(
    fundamental_db: Path,
) -> None:
    payload = _financial_payload("income")
    with get_connection() as connection:
        insert_market_record(
            connection,
            MarketRecord(
                record_id="raw-only",
                symbol="600000.SH",
                data_type=DataType.FINANCIAL_STATEMENT,
                event_time=datetime(
                    2025, 4, 30, 18, tzinfo=TZ
                ),
                source_name="raw-only",
                source_level=SourceLevel.STRUCTURED,
                data=payload,
            ),
        )
    result = _decision(_service(), auto_fetch=False)
    assert "raw-only" not in result.source_record_ids


def test_external_failure_degrades_with_finite_retries(
    fundamental_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "fundamental_fetch_max_attempts",
        2,
    )
    fetcher = CountingFetcher(error=RuntimeError("offline"))
    result = _research(_service(fetcher))
    assert fetcher.calls == 2
    assert result.status == FundamentalDataStatus.UNAVAILABLE
    assert result.auto_fetch_result == "FAILED:RuntimeError"


def test_conflict_is_excluded_from_formal_calculation(
    fundamental_db: Path,
) -> None:
    _seed_financial(status=VerificationStatus.CONFLICT)
    result = _decision(_service(), auto_fetch=False)
    assert result.status == FundamentalDataStatus.CONFLICT
    assert FundamentalRiskFlag.FINANCIAL_CONFLICT in result.risk_flags


def test_single_source_reduces_confidence(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    _seed_market(
        event_time=datetime(2025, 4, 29, 15, tzinfo=TZ),
        close=10,
    )
    result = _decision(_service(), auto_fetch=False)
    assert result.factor_output is not None
    assert result.factor_output.confidence < 0.6


def test_missing_metrics_remain_none_not_zero(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    result = _decision(_service(), auto_fetch=False)
    assert result.metrics.roe is None
    assert result.metrics.revenue_growth is None
    assert result.metrics.pe_ttm is None


def test_all_missing_never_manufactures_positive_score(
    fundamental_db: Path,
) -> None:
    result = _decision(_service(), auto_fetch=False)
    assert result.factor_output is not None
    assert result.factor_output.score == 0
    assert result.factor_output.confidence == 0


def test_peg_invalid_denominator_is_unavailable(
    fundamental_db: Path,
) -> None:
    metrics, _, flags, _ = calculate_fundamental_metrics(
        records=[],
        valuation_point=None,
    )
    assert metrics.peg is None
    assert FundamentalRiskFlag.DATA_GAP in flags


def test_growth_uses_comparable_prior_period(
    fundamental_db: Path,
) -> None:
    _seed_financial(values={"revenue": 1_200, "net_income": 120})
    _seed_financial(
        report_period="20231231",
        announcement_date="20240430",
        values={"revenue": 1_000, "net_income": 100},
    )
    result = _decision(_service(), auto_fetch=False)
    assert result.metrics.revenue_growth == pytest.approx(0.2)
    assert result.metrics.net_profit_growth == pytest.approx(0.2)


def test_cumulative_and_single_period_are_not_mixed(
    fundamental_db: Path,
) -> None:
    _seed_financial(end_type="4")
    _seed_financial(
        report_period="20231231",
        announcement_date="20240430",
        end_type="1",
    )
    result = _decision(_service(), auto_fetch=False)
    assert result.metrics.revenue_growth is None


def test_user_input_is_audited_as_user_provided(
    fundamental_db: Path,
) -> None:
    snapshot = FundamentalSnapshot(as_of=date(2024, 12, 31), roe=0.2)
    result = _research(
        _service(),
        auto_fetch=False,
        manual_snapshot=snapshot,
    )
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT source_type, verification_status
            FROM manual_fundamental_inputs
            WHERE manual_input_id = ?
            """,
            [result.manual_input_id],
        ).fetchone()
    assert row == ("USER_PROVIDED", "UNVERIFIED")


def test_manual_input_does_not_override_existing_canonical(
    fundamental_db: Path,
) -> None:
    _seed_financial(values={"liabilities": 400, "assets": 1000})
    result = _research(
        _service(),
        auto_fetch=False,
        manual_snapshot=FundamentalSnapshot(
            as_of=date(2024, 12, 31),
            debt_ratio=0.99,
        ),
    )
    assert result.metrics.debt_ratio == pytest.approx(0.4)


def test_explicit_manual_decision_override_requires_reason() -> None:
    with pytest.raises(ValidationError, match="override reason"):
        DecisionFromDataRequest(
            symbol="600000",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 1),
            portfolio=PortfolioState(cash=1, equity=1),
            fundamentals=FundamentalSnapshot(
                as_of=date(2024, 12, 31),
                roe=0.2,
            ),
            allow_user_fundamental_override=True,
            user_fundamental_operator_confirmed=True,
        )


def test_explicit_manual_override_saves_reason_and_confirmation(
    fundamental_db: Path,
) -> None:
    result = _decision(
        _service(),
        auto_fetch=False,
        manual_snapshot=FundamentalSnapshot(
            as_of=date(2024, 12, 31),
            roe=0.2,
        ),
        allow_manual_override=True,
        manual_override_reason="official filing pending verification",
        manual_operator_confirmed=True,
    )
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT override_reason, operator_confirmed,
                   accepted_for_analysis
            FROM manual_fundamental_inputs
            WHERE manual_input_id = ?
            """,
            [result.manual_input_id],
        ).fetchone()
    assert row == (
        "official filing pending verification",
        True,
        True,
    )


def test_manual_override_is_factor_evidence(
    fundamental_db: Path,
) -> None:
    result = _decision(
        _service(),
        auto_fetch=False,
        manual_snapshot=FundamentalSnapshot(
            as_of=date(2024, 12, 31),
            roe=0.2,
        ),
        allow_manual_override=True,
        manual_override_reason="confirmed input",
        manual_operator_confirmed=True,
    )
    assert result.factor_output is not None
    assert result.manual_input_id in result.factor_output.evidence_ids


def test_unverified_manual_input_reduces_confidence(
    fundamental_db: Path,
) -> None:
    result = _decision(
        _service(),
        auto_fetch=False,
        manual_snapshot=FundamentalSnapshot(
            as_of=date(2024, 12, 31),
            roe=0.2,
        ),
        allow_manual_override=True,
        manual_override_reason="confirmed input",
        manual_operator_confirmed=True,
    )
    assert result.factor_output is not None
    assert result.factor_output.confidence <= 0.2


def _daily_records() -> list[MarketRecord]:
    records: list[MarketRecord] = []
    for index in range(40):
        trade_date = date(2025, 3, 1) + timedelta(days=index)
        close = 10 + index * 0.01
        payload = {
            "trade_date": trade_date.strftime("%Y%m%d"),
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 1000,
        }
        records.append(
            MarketRecord(
                record_id=f"decision-bar-{index}",
                symbol="600000.SH",
                data_type=DataType.DAILY_BAR,
                event_time=datetime.combine(
                    trade_date,
                    datetime.min.time(),
                    tzinfo=TZ,
                ).replace(hour=15),
                fetched_at=NOW,
                source_name="decision-test",
                source_level=SourceLevel.STRUCTURED,
                verified=True,
                content_hash=_hash(payload),
                data=payload,
            )
        )
    return records


def _decision_service(
    fundamental_db: Path,
    *,
    financial_service: FundamentalAnalysisService | None = None,
) -> DecisionService:
    records = _daily_records()
    with get_connection() as connection:
        for record in records:
            insert_market_record(connection, record)
    service = DecisionService(DecisionRepository())
    service.daily_bars_service_factory = lambda: SimpleNamespace(
        get_daily_bars=lambda *args, **kwargs: SimpleNamespace(
            symbol="600000.SH",
            records=records,
        )
    )
    if financial_service is not None:
        service.fundamental_service = DecisionFundamentalService(
            financial_service
        )
    return service


@pytest.mark.asyncio
async def test_decision_packet_tracks_fundamental_factor(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    service = _decision_service(
        fundamental_db,
        financial_service=_service(),
    )
    packet = await service.create_decision_from_data(
        DecisionFromDataRequest(
            symbol="600000",
            start_date=date(2025, 3, 1),
            end_date=date(2025, 5, 1),
            data_cutoff=CUTOFF,
            auto_fetch_fundamental=False,
            portfolio=PortfolioState(cash=900, equity=1000),
        )
    )
    assert packet.factor_output_id in packet.factor_output_ids
    assert packet.used_report_period == "20241231"


def test_factor_output_hash_is_deterministic(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    first = _decision(_service(), auto_fetch=False)
    second = _decision(_service(), auto_fetch=False)
    assert first.factor_output is not None
    assert second.factor_output is not None
    assert first.factor_output.factor_id == second.factor_output.factor_id
    assert len(first.factor_output.input_snapshot_hash) == 64


def test_original_technical_and_risk_modules_are_unchanged() -> None:
    from trading.decision_support.orchestrator.service import (
        _generate_strategy,
    )
    from trading.schemas import Signal

    proposal = _generate_strategy(
        Signal(
            role="technical",
            score=1,
            confidence=1,
            summary="technical",
        ),
        Signal(
            role="fundamental",
            score=0,
            confidence=0,
            summary="fundamental",
        ),
    )
    assert proposal.action.value == "buy"
    assert proposal.target_weight == pytest.approx(0.12)


def test_formal_weights_remain_sixty_forty() -> None:
    from trading.decision_support.orchestrator.service import (
        _generate_strategy,
    )
    from trading.schemas import Signal

    proposal = _generate_strategy(
        Signal(role="t", score=0, confidence=1, summary="t"),
        Signal(role="f", score=1, confidence=0, summary="f"),
    )
    assert proposal.action.value == "buy"
    assert proposal.target_weight == pytest.approx(0.08)


def test_old_api_request_shape_remains_valid() -> None:
    request = DecisionFromDataRequest(
        symbol="600000",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 3, 1),
        portfolio=PortfolioState(cash=1, equity=1),
    )
    assert request.analysis_mode == AnalysisMode.DECISION
    assert request.strict_point_in_time is True
    assert request.auto_fetch_fundamental is True


def test_migration_is_idempotent_twice(tmp_path: Path) -> None:
    path = tmp_path / "migration.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE canonical_financial_records (
                canonical_record_id VARCHAR PRIMARY KEY,
                symbol VARCHAR,
                payload_json JSON
            )
            """
        )
        assert apply_migration(connection) is True
        first_columns = connection.execute(
            "PRAGMA table_info('canonical_financial_records')"
        ).fetchall()
        assert apply_migration(connection) is False
        assert (
            connection.execute(
                "PRAGMA table_info('canonical_financial_records')"
            ).fetchall()
            == first_columns
        )


def test_analysis_does_not_modify_raw_record_count(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    with get_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM data_records"
        ).fetchone()[0]
    _decision(_service(), auto_fetch=False)
    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM data_records"
        ).fetchone()[0]
    assert after == before


def test_analysis_does_not_overwrite_canonical_history(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    with get_connection() as connection:
        before = connection.execute(
            """
            SELECT canonical_record_id, content_hash, payload_json
            FROM canonical_financial_records
            ORDER BY canonical_record_id
            """
        ).fetchall()
    _decision(_service(), auto_fetch=False)
    with get_connection() as connection:
        after = connection.execute(
            """
            SELECT canonical_record_id, content_hash, payload_json
            FROM canonical_financial_records
            ORDER BY canonical_record_id
            """
        ).fetchall()
    assert after == before


def test_financial_revision_links_to_previous_canonical_version(
    fundamental_db: Path,
) -> None:
    _seed_financial(announcement_date="20250429")
    _seed_financial(announcement_date="20250430")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT statement_type, revision_of_record_id
            FROM canonical_financial_records
            WHERE announcement_time = ?
            ORDER BY statement_type
            """,
            [datetime(2025, 4, 30, 18, tzinfo=TZ)],
        ).fetchall()
    assert len(rows) == 3
    assert all(row[1] is not None for row in rows)


@pytest.mark.asyncio
async def test_manual_override_is_recorded_in_decision_packet(
    fundamental_db: Path,
) -> None:
    service = _decision_service(
        fundamental_db,
        financial_service=_service(),
    )
    packet = await service.create_decision_from_data(
        DecisionFromDataRequest(
            symbol="600000",
            start_date=date(2025, 3, 1),
            end_date=date(2025, 5, 1),
            data_cutoff=CUTOFF,
            auto_fetch_fundamental=False,
            fundamentals=FundamentalSnapshot(
                as_of=date(2024, 12, 31),
                roe=0.2,
            ),
            allow_user_fundamental_override=True,
            user_fundamental_override_reason="operator confirmed",
            user_fundamental_operator_confirmed=True,
            portfolio=PortfolioState(cash=900, equity=1000),
        )
    )
    assert packet.manual_fundamental_audit_id is not None
    assert "USER_PROVIDED_DATA" in packet.risk_flags


def test_factor_evidence_resolves_to_all_layers(
    fundamental_db: Path,
) -> None:
    _seed_financial()
    result = _decision(_service(), auto_fetch=False)
    assert result.factor_output is not None
    assert all(
        FactorOutputRepository.resolve_evidence(evidence_id)
        is not None
        for evidence_id in result.factor_output.evidence_ids
    )
