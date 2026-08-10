from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from config.settings import settings
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from database.db import get_connection, initialize_database, insert_market_record
from router.api.app import app
from trading.decision_support.decision_packets import DecisionRepository
from trading.decision_support.orchestrator import DecisionService
from trading.simulation.persistence import TradingAuditStore
import trading.routes as trading_routes
from trading.routes import paper_trading
from trading.schemas import DecisionFromDataRequest, PortfolioState


def _market_records(count: int = 60) -> list[MarketRecord]:
    records: list[MarketRecord] = []
    for index in range(count):
        trade_date = date(2025, 1, 1) + timedelta(days=index)
        close = 10 + index * 0.05
        records.append(
            MarketRecord(
                record_id=f"trading-api-record-{index}",
                symbol="600000.SH",
                data_type=DataType.DAILY_BAR,
                event_time=datetime.fromisoformat(
                    f"{trade_date.isoformat()}T15:00:00+08:00"
                ),
                source_name="trading-api-test",
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
        )
    return records


def _install_decision_service(monkeypatch, records: list[MarketRecord]) -> None:
    initialize_database()
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
    monkeypatch.setattr(trading_routes, "decision_service", service)


def test_trading_routes_persist_decision_and_review(tmp_path, monkeypatch):
    original_path = settings.opc_database_path
    settings.opc_database_path = tmp_path / "trading-api.duckdb"
    paper_trading.cash = 1_000_000
    paper_trading.positions.clear()
    paper_trading.orders.clear()
    try:
        records = _market_records()
        _install_decision_service(monkeypatch, records)
        client = TestClient(app)
        response = client.post(
            "/v1/trading/decisions",
            json={
                "symbol": "600000",
                "bars": [
                    {
                        **record.data,
                        "trade_date": datetime.strptime(
                            record.data["trade_date"],
                            "%Y%m%d",
                        ).date().isoformat(),
                    }
                    for record in records
                ],
                "portfolio": {"cash": 900000, "equity": 1000000},
                "evidence_refs": [
                    f"data_record:{record.record_id}"
                    for record in records
                ],
            },
        )
        assert response.status_code == 200
        assert (
            response.json()["decision"]["opinions"][0]["role"]
            == "technical_agent"
        )

        review = client.get(f"/v1/trading/reviews/daily/{date.today().isoformat()}")
        assert review.status_code == 200
        assert len(review.json()["traces"]) == 1
    finally:
        settings.opc_database_path = original_path


def test_decision_from_data_bridges_verified_records_to_agents(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "opc_database_path", tmp_path / "from-data.duckdb")
    records = _market_records()
    _install_decision_service(monkeypatch, records)
    from trading.routes import create_decision_from_data

    import asyncio

    packet = asyncio.run(
        create_decision_from_data(
            DecisionFromDataRequest(
                symbol="600000",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 3, 1),
                portfolio=PortfolioState(cash=900_000, equity=1_000_000),
            )
        )
    )
    assert len(packet.source_record_ids) == 60
    assert packet.symbol == "600000.SH"
    assert packet.hash_is_valid()


def test_paper_account_snapshot_survives_store_reload(tmp_path):
    original_path = settings.opc_database_path
    settings.opc_database_path = tmp_path / "paper-state.duckdb"
    try:
        store = TradingAuditStore()
        account = paper_trading.account().model_copy(
            update={"cash": 123_456, "kill_switch": True}
        )
        store.record_paper_account(account)
        restored = TradingAuditStore().load_paper_account()
        assert restored is not None
        assert restored.cash == 123_456
        assert restored.kill_switch is True
    finally:
        settings.opc_database_path = original_path
