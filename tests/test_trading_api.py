from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from config.settings import settings
from router.api.app import app
from trading.routes import paper_broker
from trading.schemas import DecisionFromDataRequest, PortfolioState
from trading.persistence import TradingAuditStore


def test_trading_routes_persist_decision_and_live_order_is_locked(tmp_path):
    original_path = settings.opc_database_path
    settings.opc_database_path = tmp_path / "trading-api.duckdb"
    paper_broker.cash = 1_000_000
    paper_broker.positions.clear()
    paper_broker.orders.clear()
    try:
        client = TestClient(app)
        bars = []
        for index in range(60):
            close = 10 + index * 0.05
            bars.append(
                {
                    "trade_date": date(2025, 1, 1).toordinal() + index,
                    "open": close - 0.02,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "volume": 1_000_000,
                }
            )
            bars[-1]["trade_date"] = date.fromordinal(bars[-1]["trade_date"]).isoformat()
        response = client.post(
            "/v1/trading/decisions",
            json={
                "symbol": "600000",
                "bars": bars,
                "portfolio": {"cash": 900000, "equity": 1000000},
            },
        )
        assert response.status_code == 200
        assert response.json()["opinions"][0]["role"] == "technical_agent"

        review = client.get(f"/v1/trading/reviews/daily/{date.today().isoformat()}")
        assert review.status_code == 200
        assert len(review.json()["traces"]) == 1

        live = client.post(
            "/v1/trading/live/orders",
            json={
                "intent": {
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "reference_price": 10,
                    "price_time": datetime.now().astimezone().isoformat(),
                    "approved_by": "tester",
                }
            },
        )
        assert live.status_code == 423
        assert "Live trading is disabled" in live.json()["detail"]
    finally:
        settings.opc_database_path = original_path


def test_decision_from_data_bridges_verified_records_to_agents(monkeypatch):
    records = []
    for index in range(60):
        close = 10 + index * 0.05
        records.append(
            SimpleNamespace(
                record_id=f"record-{index}",
                data={
                    "trade_date": (date(2025, 1, 1) + timedelta(days=index)).strftime("%Y%m%d"),
                    "open": close - 0.02,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "volume": 1_000_000,
                },
            )
        )

    class FakeDailyBarsService:
        def get_daily_bars(self, *args, **kwargs):
            return SimpleNamespace(symbol="600000.SH", records=records)

    monkeypatch.setattr("trading.routes.DailyBarsService", FakeDailyBarsService)
    monkeypatch.setattr("trading.routes.audit_store.record_trace", lambda trace: None)

    from trading.routes import create_decision_from_data

    import asyncio

    trace = asyncio.run(
        create_decision_from_data(
            DecisionFromDataRequest(
                symbol="600000",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 3, 1),
                portfolio=PortfolioState(cash=900_000, equity=1_000_000),
            )
        )
    )
    assert len(trace.evidence_refs) >= 60
    assert trace.symbol == "600000.SH"


def test_paper_account_snapshot_survives_store_reload(tmp_path):
    original_path = settings.opc_database_path
    settings.opc_database_path = tmp_path / "paper-state.duckdb"
    try:
        store = TradingAuditStore()
        account = paper_broker.account().model_copy(update={"cash": 123_456, "kill_switch": True})
        store.record_paper_account(account)
        restored = TradingAuditStore().load_paper_account()
        assert restored is not None
        assert restored.cash == 123_456
        assert restored.kill_switch is True
    finally:
        settings.opc_database_path = original_path
