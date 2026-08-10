from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import trading.routes as trading_routes
from config.settings import settings
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from database.db import get_connection, initialize_database, insert_market_record
from router.api.app import app
from scripts.check_no_live_execution import (
    CURRENT_ROUTE_METHODS,
    STAGE2_DEPRECATED_ROUTE_METHODS,
    STAGE5_CANONICAL_ROUTE_METHODS,
    load_openapi_schema,
    openapi_issues,
    route_methods,
)
from trading.decision_support.decision_packets import DecisionRepository
from trading.decision_support.orchestrator import DecisionService
from manual_tracking.risk_reviews import (
    ManualPositionRiskReviewRepository,
)
from manual_tracking.trades import ManualTradeRepository
from trading.simulation.persistence import TradingAuditStore
from trading.review.daily import build_review_service
from trading.simulation.service import SimulationService


def _bars(count: int = 70) -> list[dict[str, Any]]:
    start = date(2025, 1, 1)
    result: list[dict[str, Any]] = []
    for index in range(count):
        close = 10 + index * 0.05
        result.append(
            {
                "trade_date": (start + timedelta(days=index)).isoformat(),
                "open": close - 0.02,
                "high": close + 0.10,
                "low": close - 0.10,
                "close": close,
                "volume": 1_000_000,
            }
        )
    return result


def _decision_from_data_payload() -> dict[str, Any]:
    return {
        "symbol": "600000",
        "start_date": "2025-01-01",
        "end_date": "2025-03-31",
        "portfolio": {"cash": 900_000, "equity": 1_000_000},
    }


def _decision_payload() -> dict[str, Any]:
    return {
        "symbol": "600000",
        "bars": _bars(),
        "portfolio": {"cash": 900_000, "equity": 1_000_000},
        "evidence_refs": [
            f"data_record:stage2-record-{index}"
            for index in range(len(_bars()))
        ],
    }


def _backtest_payload() -> dict[str, Any]:
    return {"symbol": "600000", "bars": _bars()}


def _optimization_payload() -> dict[str, Any]:
    return {
        "assets": [
            {"symbol": "600000", "expected_score": 0.8, "volatility": 0.2},
            {"symbol": "000001", "expected_score": 0.4, "volatility": 0.3},
        ],
        "max_position_weight": 0.3,
        "max_gross_exposure": 0.5,
    }


def _paper_order_payload(client_order_id: str = "stage2-paper-order") -> dict[str, Any]:
    return {
        "intent": {
            "client_order_id": client_order_id,
            "symbol": "600000",
            "side": "buy",
            "quantity": 100,
            "reference_price": 10,
            "price_time": datetime.now().astimezone().isoformat(),
            "approved_by": "stage2-test",
        }
    }


def _fake_daily_bar_records() -> list[MarketRecord]:
    return [
        MarketRecord(
            record_id=f"stage2-record-{index}",
            symbol="600000.SH",
            data_type=DataType.DAILY_BAR,
            event_time=datetime.fromisoformat(f"{bar['trade_date']}T15:00:00+08:00"),
            source_name="stage2-test",
            source_level=SourceLevel.STRUCTURED,
            verified=True,
            data={
                **bar,
                "trade_date": str(bar["trade_date"]).replace("-", ""),
            },
        )
        for index, bar in enumerate(_bars())
    ]


@pytest.fixture
def api_services(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "opc_database_path", tmp_path / "stage2-api.duckdb")
    initialize_database()
    records = _fake_daily_bar_records()
    with get_connection() as connection:
        for record in records:
            insert_market_record(connection, record)
    fake_daily_bars_service = SimpleNamespace(
        get_daily_bars=lambda *args, **kwargs: SimpleNamespace(
            symbol="600000.SH",
            records=records,
        )
    )
    store = TradingAuditStore()
    decision_repository = DecisionRepository()
    decision_service = DecisionService(decision_repository)
    decision_service.daily_bars_service_factory = lambda: fake_daily_bars_service
    simulation_service = SimulationService(audit_store=store)
    review_service = build_review_service(
        decisions=decision_repository,
        simulations=store,
        manual_trades=ManualTradeRepository(),
        risk_reviews=ManualPositionRiskReviewRepository(),
    )
    monkeypatch.setattr(trading_routes, "audit_store", store)
    monkeypatch.setattr(
        trading_routes,
        "decision_service",
        decision_service,
    )
    monkeypatch.setattr(trading_routes, "simulation_service", simulation_service)
    monkeypatch.setattr(trading_routes, "review_service", review_service)
    monkeypatch.setattr(
        trading_routes,
        "paper_trading",
        simulation_service.paper_trading,
    )
    return {
        "client": TestClient(app),
        "decision": decision_service,
        "simulation": simulation_service,
        "review": review_service,
    }


def test_new_decision_routes(api_services) -> None:
    client = api_services["client"]
    response = client.post(
        "/v1/decisions/from-data",
        json=_decision_from_data_payload(),
    )

    assert response.status_code == 200
    assert response.json()["symbol"] == "600000.SH"
    assert len(response.json()["source_record_ids"]) == len(_bars())
    assert "Deprecation" not in response.headers
    decision_id = response.json()["decision_id"]
    latest = client.get(f"/v1/decisions/{decision_id}")
    versions = client.get(f"/v1/decisions/{decision_id}/versions")
    version = client.get(f"/v1/decisions/{decision_id}/versions/1")
    challenge = client.post(
        f"/v1/decisions/{decision_id}/challenge",
        json={"challenge": "Check whether momentum is overstated."},
    )
    assert latest.status_code == 200
    assert latest.json() == response.json()
    assert versions.status_code == 200
    assert versions.json() == [response.json()]
    assert version.status_code == 200
    assert version.json() == response.json()
    assert challenge.status_code == 200
    assert challenge.json()["created_version"] is None


def test_new_simulation_routes(api_services) -> None:
    client = api_services["client"]

    backtest = client.post("/v1/simulations/backtests", json=_backtest_payload())
    optimization = client.post(
        "/v1/simulations/portfolio/optimize",
        json=_optimization_payload(),
    )
    order = client.post(
        "/v1/simulations/paper-orders",
        json=_paper_order_payload(),
    )
    account = client.get("/v1/simulations/paper-account")
    exits = client.post(
        "/v1/simulations/protective-exits",
        json={"prices": {"600000": 10}},
    )
    kill_switch = client.post(
        "/v1/simulations/kill-switch",
        json={"active": True},
    )

    assert backtest.status_code == 200
    assert backtest.json()["symbol"] == "600000"
    assert optimization.status_code == 200
    assert sum(optimization.json()["weights"].values()) <= 0.50000001
    assert order.status_code == 200
    assert order.json()["status"] == "filled"
    assert account.status_code == 200
    assert account.json()["positions"]["600000"]["quantity"] == 100
    assert exits.status_code == 200
    assert exits.json() == []
    assert kill_switch.status_code == 200
    assert kill_switch.json()["kill_switch"] is True


def test_new_review_routes(api_services) -> None:
    client = api_services["client"]
    review_date = date.today().isoformat()

    created = client.post("/v1/reviews/daily", json={"date": review_date})
    fetched = client.get(f"/v1/reviews/daily/{review_date}")
    markdown = client.get(f"/v1/reviews/daily/{review_date}/markdown")

    assert created.status_code == 200
    assert fetched.status_code == 200
    assert created.json() == fetched.json()
    assert markdown.status_code == 200
    assert f"每日决策复盘：{review_date}" in markdown.text


def test_deprecated_routes_call_same_services(api_services, monkeypatch) -> None:
    client = api_services["client"]
    decision_service = api_services["decision"]
    simulation_service = api_services["simulation"]
    review_service = api_services["review"]
    calls = {"decision": 0, "backtest": 0, "review": 0}

    original_decision = decision_service.create_decision_from_data

    async def tracked_decision(request):
        calls["decision"] += 1
        return await original_decision(request)

    original_backtest = simulation_service.backtest

    def tracked_backtest(request):
        calls["backtest"] += 1
        return original_backtest(request)

    original_review = review_service.daily_review

    def tracked_review(review_date):
        calls["review"] += 1
        return original_review(review_date)

    monkeypatch.setattr(decision_service, "create_decision_from_data", tracked_decision)
    monkeypatch.setattr(simulation_service, "backtest", tracked_backtest)
    monkeypatch.setattr(review_service, "daily_review", tracked_review)

    new_decision = client.post(
        "/v1/decisions/from-data",
        json=_decision_from_data_payload(),
    )
    old_decision = client.post(
        "/v1/trading/decisions/from-data",
        json=_decision_from_data_payload(),
    )
    new_backtest = client.post(
        "/v1/simulations/backtests",
        json=_backtest_payload(),
    )
    old_backtest = client.post(
        "/v1/trading/backtests",
        json=_backtest_payload(),
    )
    review_date = date.today().isoformat()
    new_review = client.post("/v1/reviews/daily", json={"date": review_date})
    old_review = client.get(f"/v1/trading/reviews/daily/{review_date}")

    assert calls == {"decision": 2, "backtest": 2, "review": 2}
    assert new_backtest.json() == old_backtest.json()
    assert new_review.json() == old_review.json()
    new_business_result = new_decision.json()
    old_business_result = old_decision.json()
    for transient_field in ("decision_id", "generated_at", "packet_hash"):
        new_business_result.pop(transient_field)
        old_business_result.pop(transient_field)
    for transient_field in ("trace_id", "created_at"):
        new_business_result["decision"].pop(transient_field)
        old_business_result["decision"].pop(transient_field)
    assert new_business_result == old_business_result


def test_deprecated_headers(api_services) -> None:
    client = api_services["client"]
    review_date = date.today().isoformat()
    calls = [
        ("post", "/v1/trading/decisions", _decision_payload()),
        ("post", "/v1/trading/decisions/from-data", _decision_from_data_payload()),
        ("post", "/v1/trading/backtests", _backtest_payload()),
        ("post", "/v1/trading/portfolio/optimize", _optimization_payload()),
        ("post", "/v1/trading/paper/orders", _paper_order_payload("legacy-order")),
        ("get", "/v1/trading/paper/account", None),
        ("post", "/v1/trading/paper/protective-exits", {"prices": {"600000": 10}}),
        ("post", "/v1/trading/paper/kill-switch", {"active": False}),
        ("get", f"/v1/trading/reviews/daily/{review_date}", None),
        ("get", f"/v1/trading/reviews/daily/{review_date}/markdown", None),
    ]

    for method, path, payload in calls:
        response = client.request(method, path, json=payload)
        assert response.status_code == 200, path
        assert response.headers["Deprecation"] == "true", path
        assert response.headers["Sunset"] == "Thu, 31 Dec 2026 23:59:59 GMT", path

    invalid = client.post("/v1/trading/backtests", json={})
    assert invalid.status_code == 422
    assert invalid.headers["Deprecation"] == "true"
    assert invalid.headers["Sunset"] == "Thu, 31 Dec 2026 23:59:59 GMT"


def test_no_live_routes_reintroduced() -> None:
    schema = load_openapi_schema()
    assert openapi_issues(schema) == []
    assert all(
        forbidden not in path.lower()
        for path in schema["paths"]
        for forbidden in ("/live", "/broker")
    )


def test_openapi_route_allowlist() -> None:
    schema = load_openapi_schema()
    assert route_methods(schema) == CURRENT_ROUTE_METHODS
    for method, path in STAGE2_DEPRECATED_ROUTE_METHODS:
        operation = schema["paths"][path][method.lower()]
        assert operation["deprecated"] is True
        headers = operation["responses"]["200"]["headers"]
        assert {"Deprecation", "Sunset"} <= headers.keys()
    for method, path in STAGE5_CANONICAL_ROUTE_METHODS:
        operation = schema["paths"][path][method.lower()]
        assert operation.get("deprecated") is not True
