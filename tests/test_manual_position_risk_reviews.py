from __future__ import annotations

import asyncio
import inspect
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import trading.routes as trading_routes
from config.settings import settings
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from database.db import get_connection, initialize_database, insert_market_record
from router.api.app import app
from trading.decision_support.decision_packets import DecisionRepository
from trading.decision_support.orchestrator import DecisionService
from manual_tracking.risk_reviews import (
    ManualPositionRiskReviewRepository,
)
from manual_tracking.risk_reviews import (
    HighRiskModelReview,
    ManualPositionRiskReviewService,
)
from manual_tracking.trades import ManualTradeRepository
from manual_tracking.trades import ManualTradeService
from trading.simulation.persistence import TradingAuditStore
from trading.research.events.position_data import (
    PersistedResearchRecord,
    PositionResearchSnapshot,
)
from trading.review.attribution import (
    DecisionReadRepository,
    ManualPositionRiskReviewReadRepository,
    ManualTradeReadRepository,
    SimulationReadRepository,
)
from trading.review.daily import ReviewService
from trading.schemas import (
    DecisionRequest,
    ManualPositionRiskReview,
    ManualPositionRiskReviewRequest,
    ManualRiskRecommendedAction,
    ManualTradeInput,
    ManualTradeSide,
    PortfolioState,
)
from trading.simulation.service import SimulationService


class FakeResearchReader:
    def __init__(self, records: list[PersistedResearchRecord]) -> None:
        self.records = tuple(records)

    async def refresh(
        self,
        *,
        symbol: str,
        reviewed_at: datetime,
        lookback_days: int,
    ) -> PositionResearchSnapshot:
        assert symbol == "600000.SH"
        assert lookback_days >= 30
        assert reviewed_at.tzinfo is not None
        return PositionResearchSnapshot(
            records=self.records,
            missing_information=(
                "News records remain unverified media evidence",
            ),
        )


class FakeHighRiskReviewer:
    async def review(self, **kwargs) -> HighRiskModelReview:
        assert kwargs["risk_level"].value in {"HIGH", "CRITICAL"}
        return HighRiskModelReview(
            decision="revise",
            assessed_risk_level=kwargs["risk_level"].value.lower(),
            confidence=0.82,
            finding_count=2,
            call_ids=(),
        )


def _insert_records() -> tuple[list[MarketRecord], list[PersistedResearchRecord]]:
    now = datetime.now().astimezone()
    market_records = [
        MarketRecord(
            record_id="risk-quote",
            symbol="600000.SH",
            data_type=DataType.REALTIME_QUOTE,
            event_time=now - timedelta(minutes=2),
            source_name="test-quote",
            source_level=SourceLevel.STRUCTURED,
            verified=True,
            data={"quote_type": "test", "price": 8.0},
        ),
        MarketRecord(
            record_id="risk-announcement",
            symbol="600000.SH",
            data_type=DataType.ANNOUNCEMENT,
            event_time=now - timedelta(hours=1),
            source_name="test-official",
            source_level=SourceLevel.OFFICIAL,
            verified=True,
            data={
                "title": "重大违法退市风险提示",
                "category": "risk",
            },
        ),
        MarketRecord(
            record_id="risk-news",
            symbol="600000.SH",
            data_type=DataType.FINANCE_NEWS,
            event_time=now - timedelta(hours=2),
            source_name="test-media",
            source_level=SourceLevel.MEDIA,
            verified=False,
            data={"title": "Market commentary", "publisher": "test"},
        ),
        MarketRecord(
            record_id="risk-financial",
            symbol="600000.SH",
            data_type=DataType.FINANCIAL_STATEMENT,
            event_time=now - timedelta(days=5),
            source_name="test-financial",
            source_level=SourceLevel.OFFICIAL,
            verified=True,
            data={
                "statement_type": "income_statement",
                "report_period": "20260630",
            },
        ),
    ]
    with get_connection() as connection:
        for record in market_records:
            insert_market_record(connection, record)
    persisted = [
        PersistedResearchRecord(
            record_id=record.record_id,
            symbol=record.symbol,
            data_type=record.data_type.value,
            event_time=record.event_time,
            fetched_at=record.fetched_at,
            source_name=record.source_name,
            source_level=record.source_level.value,
            verified=record.verified,
            payload=record.data,
        )
        for record in market_records
    ]
    return market_records, persisted


def _seed_decision(repository: DecisionRepository) -> str:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=29)
    bars: list[dict[str, object]] = []
    record_ids: list[str] = []
    with get_connection() as connection:
        for index in range(30):
            trade_date = start + timedelta(days=index)
            close = 9.0 + index * 0.04
            record = MarketRecord(
                record_id=f"risk-decision-{index}",
                symbol="600000.SH",
                data_type=DataType.DAILY_BAR,
                event_time=datetime.fromisoformat(
                    f"{trade_date.isoformat()}T15:00:00+08:00"
                ),
                source_name="risk-decision-test",
                source_level=SourceLevel.STRUCTURED,
                verified=True,
                data={
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "open": close - 0.02,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "volume": 1_000_000,
                },
            )
            insert_market_record(connection, record)
            record_ids.append(record.record_id)
            bars.append(
                {
                    **record.data,
                    "trade_date": trade_date,
                }
            )
    packet = asyncio.run(
        DecisionService(repository).create_decision(
            DecisionRequest(
                symbol="600000.SH",
                bars=bars,
                portfolio=PortfolioState(
                    cash=900_000,
                    equity=1_000_000,
                ),
                evidence_refs=[
                    f"data_record:{record_id}" for record_id in record_ids
                ],
            )
        )
    )
    return packet.decision_id


@pytest.fixture
def risk_context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "opc_database_path", tmp_path / "risk-review.duckdb")
    initialize_database()
    _, persisted = _insert_records()
    decision_repository = DecisionRepository()
    decision_id = _seed_decision(decision_repository)
    manual_repository = ManualTradeRepository()
    manual_service = ManualTradeService(manual_repository, decision_repository)
    preview = manual_service.create_preview(
        ManualTradeInput(
            portfolio_id="portfolio-main",
            client_trade_id="risk-external-trade",
            symbol="600000.SH",
            side=ManualTradeSide.BUY,
            quantity=100,
            price=10,
            fees=5,
            taxes=1,
            traded_at=datetime.now().astimezone() - timedelta(minutes=5),
            decision_id=decision_id,
            notes="User-reported external fact.",
        ),
        requested_by="user-1",
        channel="wecom",
    )
    manual_service.confirm_preview(
        preview.confirmation_id,
        confirmed_by="user-1",
        channel="wecom",
    )
    risk_repository = ManualPositionRiskReviewRepository()
    service = ManualPositionRiskReviewService(
        manual_trades=ManualTradeReadRepository(manual_repository),
        decisions=DecisionReadRepository(decision_repository),
        reviews=risk_repository,
        research=FakeResearchReader(persisted),
        high_risk_reviewer=FakeHighRiskReviewer(),
    )
    audit_store = TradingAuditStore()
    simulation_service = SimulationService(audit_store=audit_store)
    return {
        "service": service,
        "manual_repository": manual_repository,
        "decision_repository": decision_repository,
        "risk_repository": risk_repository,
        "audit_store": audit_store,
        "simulation_service": simulation_service,
        "position": manual_repository.list_positions()[0],
    }


def _run_review(context) -> ManualPositionRiskReview:
    return asyncio.run(
        context["service"].review_position(
            context["position"].position_id,
            ManualPositionRiskReviewRequest(),
            created_by="user-1",
            channel="wecom",
        )
    )


def test_risk_review_does_not_modify_manual_position(risk_context) -> None:
    before = risk_context["manual_repository"].get_position(
        risk_context["position"].position_id
    )
    _run_review(risk_context)
    after = risk_context["manual_repository"].get_position(before.position_id)
    assert after == before


def test_risk_review_does_not_modify_simulation_account(risk_context) -> None:
    before = risk_context["simulation_service"].paper_account()
    _run_review(risk_context)
    assert risk_context["simulation_service"].paper_account() == before


def test_recommended_action_is_advisory_enum_only(risk_context) -> None:
    assert {item.value for item in ManualRiskRecommendedAction} == {
        "CONTINUE_OBSERVATION",
        "HUMAN_REVIEW_REQUIRED",
        "CONSIDER_REDUCING",
        "CONSIDER_EXITING",
    }
    review = _run_review(risk_context)
    assert review.recommended_action in set(ManualRiskRecommendedAction)


def test_risk_review_has_cutoff_and_traceable_evidence(risk_context) -> None:
    review = _run_review(risk_context)
    assert review.data_cutoff_time <= review.reviewed_at
    assert set(review.evidence_record_ids) == {
        "risk-quote",
        "risk-announcement",
        "risk-news",
        "risk-financial",
    }
    with get_connection() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_records
            WHERE record_id IN (?, ?, ?, ?)
            """,
            review.evidence_record_ids,
        ).fetchone()[0]
    assert count == len(review.evidence_record_ids)
    assert {
        source_id
        for fact in review.new_verified_facts
        for source_id in fact.source_record_ids
    } <= set(review.evidence_record_ids)


def test_risk_review_is_append_only_and_latest_is_queryable(risk_context) -> None:
    first = _run_review(risk_context)
    second = _run_review(risk_context)
    reviews = risk_context["risk_repository"].list_for_position(
        risk_context["position"].position_id
    )
    assert [review.review_id for review in reviews] == [
        first.review_id,
        second.review_id,
    ]
    assert (
        risk_context["risk_repository"]
        .latest_for_position(risk_context["position"].position_id)
        .review_id
        == second.review_id
    )
    source = inspect.getsource(ManualPositionRiskReviewRepository).lower()
    assert "update manual_position_risk_reviews" not in source
    assert "delete from manual_position_risk_reviews" not in source


def test_risk_review_contract_has_no_transaction_object() -> None:
    schema_text = str(ManualPositionRiskReview.model_json_schema()).lower()
    source = Path(
        inspect.getsourcefile(ManualPositionRiskReviewService) or ""
    ).read_text(encoding="utf-8").lower()
    for prohibited in (
        "close_position",
        "submit_order",
        "place_order",
        "auto_reduce",
    ):
        assert prohibited not in schema_text
        assert prohibited not in source
    assert "orderintent" not in schema_text
    assert "orderresult" not in schema_text


def test_unified_daily_review_uses_read_only_repositories(risk_context) -> None:
    risk_review = _run_review(risk_context)
    service = ReviewService(
        decisions=DecisionReadRepository(risk_context["decision_repository"]),
        simulations=SimulationReadRepository(risk_context["audit_store"]),
        manual_trades=ManualTradeReadRepository(
            risk_context["manual_repository"]
        ),
        risk_reviews=ManualPositionRiskReviewReadRepository(
            risk_context["risk_repository"]
        ),
    )
    review = service.daily_review(date.today())

    assert review.manual_trades
    assert review.manual_positions == [risk_context["position"]]
    assert review.manual_position_risk_reviews == [risk_review]
    assert review.decision_packets
    assert review.model_errors
    assert review.evidence_gaps
    signature = inspect.signature(ReviewService.__init__)
    assert set(signature.parameters) == {
        "self",
        "decisions",
        "simulations",
        "manual_trades",
        "risk_reviews",
    }
    public_read_methods = {
        name
        for repository_type in (
            DecisionReadRepository,
            SimulationReadRepository,
            ManualTradeReadRepository,
            ManualPositionRiskReviewReadRepository,
        )
        for name, member in inspect.getmembers(
            repository_type,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    assert not any(
        token in name
        for name in public_read_methods
        for token in ("create", "append", "confirm", "update", "delete", "write")
    )


def test_risk_review_routes_use_advisory_service(risk_context, monkeypatch) -> None:
    monkeypatch.setattr(
        trading_routes,
        "manual_position_risk_service",
        risk_context["service"],
    )
    client = TestClient(app)
    position_id = risk_context["position"].position_id
    response = client.post(
        f"/v1/manual-positions/{position_id}/risk-reviews",
        json={"lookback_days": 365, "allow_model_review": True},
        headers={
            "X-Authenticated-User": "user-1",
            "X-Authenticated-Channel": "wecom",
        },
    )
    assert response.status_code == 200
    created = response.json()
    listed = client.get(
        f"/v1/manual-positions/{position_id}/risk-reviews"
    )
    latest = client.get(
        f"/v1/manual-positions/{position_id}/risk-reviews/latest"
    )
    assert listed.status_code == 200
    assert latest.status_code == 200
    assert [item["review_id"] for item in listed.json()] == [
        created["review_id"]
    ]
    assert latest.json()["review_id"] == created["review_id"]
