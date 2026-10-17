from __future__ import annotations

from fastapi.testclient import TestClient

from config.settings import settings
from router.api.app import app
from trading.decision_support.trade_plan import TradePlan, TradePlanStatus
from trading.decision_support.trade_plan_repository import TradePlanRepository


def _plan(plan_id: str, local_user_id: str, status: TradePlanStatus) -> TradePlan:
    return TradePlan(
        plan_id=plan_id,
        local_user_id=local_user_id,
        symbol="600000.SH",
        action="BUY",
        entry_zone=[10.0, 10.5],
        stop_loss_price=9.5,
        status=status,
    )


def test_trade_plan_routes_read_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "opc_database_path", tmp_path / "trade-plans.duckdb")
    repository = TradePlanRepository()
    repository.create_plan(_plan("plan-a", "user-1", TradePlanStatus.ACTIVE))
    repository.create_plan(_plan("plan-b", "user-1", TradePlanStatus.DRAFT))
    repository.create_plan(_plan("plan-c", "user-2", TradePlanStatus.ACTIVE))

    client = TestClient(app)

    listed = client.get("/v1/trade-plans", params={"local_user_id": "user-1"})
    assert listed.status_code == 200
    plans = listed.json()
    assert [plan["plan_id"] for plan in plans] == ["plan-b", "plan-a"]

    filtered = client.get(
        "/v1/trade-plans", params={"local_user_id": "user-1", "status": "ACTIVE"}
    )
    assert filtered.status_code == 200
    assert [plan["plan_id"] for plan in filtered.json()] == ["plan-a"]

    detail = client.get("/v1/trade-plans/plan-a")
    assert detail.status_code == 200
    body = detail.json()
    assert body["symbol"] == "600000.SH"
    assert body["entry_zone"] == [10.0, 10.5]
    assert body["status"] == "ACTIVE"
    assert body["status_zh"] == "生效中"

    missing = client.get("/v1/trade-plans/plan-zzz")
    assert missing.status_code == 404

    assert client.get("/v1/trade-plans").status_code == 422
