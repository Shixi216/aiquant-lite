from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import manual_tracking.trades.service as manual_service_module
import trading.routes as trading_routes
from config.settings import settings
from database.db import get_connection, initialize_database
from router.api.app import app
from trading.decision_support.orchestrator import DecisionService
from manual_tracking.confirmations import ManualTradeChatService
from manual_tracking.trades import (
    ManualPreviewExpiredError,
    ManualPreviewIntegrityError,
    ManualTradeConflictError,
    ManualTradeIdentityError,
    ManualTradeRepository,
)
from manual_tracking.trades import ManualTradeService
from trading.simulation.persistence import TradingAuditStore
from trading.schemas import (
    ManualTradeCorrectionRequest,
    ManualTradeInput,
    ManualTradeReplacement,
    ManualTradeSide,
    OrderIntent,
    OrderSide,
    RiskLimits,
)
from trading.simulation.service import SimulationService


@pytest.fixture
def manual_context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "opc_database_path", tmp_path / "manual-trades.duckdb")
    initialize_database()
    repository = ManualTradeRepository()
    service = ManualTradeService(repository)
    return repository, service


def _trade(
    *,
    client_trade_id: str = "external-trade-1",
    quantity: int = 100,
    price: float = 10.0,
    side: ManualTradeSide = ManualTradeSide.BUY,
) -> ManualTradeInput:
    return ManualTradeInput(
        portfolio_id="portfolio-main",
        client_trade_id=client_trade_id,
        symbol="600000.SH",
        side=side,
        quantity=quantity,
        price=price,
        fees=5,
        taxes=1,
        traded_at=datetime.now().astimezone() - timedelta(minutes=5),
        notes="User reported an already completed external trade.",
    )


def _confirm(
    service: ManualTradeService,
    trade: ManualTradeInput,
    *,
    user: str = "user-1",
    channel: str = "wecom",
):
    preview = service.create_preview(
        trade,
        requested_by=user,
        channel=channel,
    )
    return service.confirm_preview(
        preview.confirmation_id,
        confirmed_by=user,
        channel=channel,
    )


def _manual_trade_count() -> int:
    with get_connection() as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM manual_trades").fetchone()[0]
        )


def test_duplicate_client_trade_id_does_not_duplicate_ledger(manual_context) -> None:
    _, service = manual_context
    _confirm(service, _trade())
    duplicate = service.create_preview(
        _trade(),
        requested_by="user-1",
        channel="wecom",
    )

    with pytest.raises(ManualTradeConflictError, match="client_trade_id"):
        service.confirm_preview(
            duplicate.confirmation_id,
            confirmed_by="user-1",
            channel="wecom",
        )
    assert _manual_trade_count() == 1


def test_repeated_confirmation_is_idempotent(manual_context) -> None:
    _, service = manual_context
    preview = service.create_preview(
        _trade(),
        requested_by="user-1",
        channel="wecom",
    )

    first = service.confirm_preview(
        preview.confirmation_id,
        confirmed_by="user-1",
        channel="wecom",
    )
    second = service.confirm_preview(
        preview.confirmation_id,
        confirmed_by="user-1",
        channel="wecom",
    )

    assert second.trade.trade_id == first.trade.trade_id
    assert second.idempotent_replay is True
    assert _manual_trade_count() == 1


def test_expired_preview_cannot_be_confirmed(
    manual_context,
    monkeypatch,
) -> None:
    _, service = manual_context
    start = datetime.now().astimezone()
    monkeypatch.setattr(manual_service_module, "_now", lambda: start)
    preview = service.create_preview(
        _trade(),
        requested_by="user-1",
        channel="wecom",
        expires_in_seconds=60,
    )
    monkeypatch.setattr(
        manual_service_module,
        "_now",
        lambda: start + timedelta(seconds=61),
    )

    with pytest.raises(ManualPreviewExpiredError):
        service.confirm_preview(
            preview.confirmation_id,
            confirmed_by="user-1",
            channel="wecom",
        )
    assert service.get_preview(preview.confirmation_id).status.value == "EXPIRED"
    assert _manual_trade_count() == 0


def test_other_user_cannot_confirm_preview(manual_context) -> None:
    _, service = manual_context
    preview = service.create_preview(
        _trade(),
        requested_by="user-1",
        channel="qq",
    )

    with pytest.raises(ManualTradeIdentityError):
        service.confirm_preview(
            preview.confirmation_id,
            confirmed_by="user-2",
            channel="qq",
        )
    with pytest.raises(ManualTradeIdentityError):
        service.confirm_preview(
            preview.confirmation_id,
            confirmed_by="user-1",
            channel="wecom",
        )
    assert _manual_trade_count() == 0


def test_tampered_preview_payload_cannot_be_confirmed(manual_context) -> None:
    _, service = manual_context
    preview = service.create_preview(
        _trade(),
        requested_by="user-1",
        channel="wecom",
    )
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE manual_trade_previews
            SET payload_json = json_merge_patch(
                payload_json,
                '{"trade":{"price":999.0}}'
            )
            WHERE confirmation_id = ?
            """,
            [preview.confirmation_id],
        )

    with pytest.raises(ManualPreviewIntegrityError, match="hash"):
        service.confirm_preview(
            preview.confirmation_id,
            confirmed_by="user-1",
            channel="wecom",
        )
    assert _manual_trade_count() == 0


def test_manual_positions_have_no_direct_write_surface(manual_context) -> None:
    repository, _ = manual_context
    with get_connection() as connection:
        table_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = 'manual_positions'
            """
        ).fetchone()[0]
    assert table_count == 0
    forbidden_methods = {
        name
        for name in dir(repository)
        if "position" in name.lower()
        and any(
            verb in name.lower()
            for verb in ("create", "insert", "update", "upsert", "delete", "write")
        )
    }
    assert forbidden_methods == set()
    schema = app.openapi()
    assert set(schema["paths"]["/v1/manual-positions"]) == {"get"}
    assert set(schema["paths"]["/v1/manual-positions/{position_id}"]) == {"get"}


def test_correction_preserves_original_trade(manual_context) -> None:
    repository, service = manual_context
    original = _confirm(service, _trade()).trade
    original_snapshot = repository.get_trade(original.trade_id)
    preview = service.create_correction_preview(
        original.trade_id,
        ManualTradeCorrectionRequest(
            correction_type="CORRECTION",
            reason="Quantity was entered incorrectly.",
            replacement=ManualTradeReplacement(
                client_trade_id="external-trade-1-correction",
                side=ManualTradeSide.BUY,
                quantity=200,
                price=10,
                fees=5,
                taxes=1,
                traded_at=original.traded_at,
                notes="Correct quantity is 200.",
            ),
        ),
        requested_by="user-1",
        channel="wecom",
    )
    assert len(repository.list_trades()) == 1
    correction = service.confirm_preview(
        preview.confirmation_id,
        confirmed_by="user-1",
        channel="wecom",
    ).trade

    assert repository.get_trade(original.trade_id) == original_snapshot
    assert correction.correction_of_trade_id == original.trade_id
    assert len(repository.list_trades()) == 2
    position = repository.list_positions()[0]
    assert position.quantity == 200
    assert position.source_trade_ids == [original.trade_id, correction.trade_id]
    with get_connection() as connection:
        correction_type = connection.execute(
            """
            SELECT correction_type
            FROM manual_trade_corrections
            WHERE correction_trade_id = ?
            """,
            [correction.trade_id],
        ).fetchone()[0]
    assert correction_type == "CORRECTION"


def test_reversal_preserves_history_and_removes_position_effect(manual_context) -> None:
    repository, service = manual_context
    original = _confirm(service, _trade()).trade
    preview = service.create_correction_preview(
        original.trade_id,
        ManualTradeCorrectionRequest(
            correction_type="REVERSAL",
            reason="This trade did not belong to this portfolio.",
            client_trade_id="external-trade-1-reversal",
        ),
        requested_by="user-1",
        channel="wecom",
    )
    assert len(repository.list_trades()) == 1
    reversal = service.confirm_preview(
        preview.confirmation_id,
        confirmed_by="user-1",
        channel="wecom",
    ).trade

    assert len(repository.list_trades()) == 2
    assert repository.get_trade(original.trade_id) == original
    assert reversal.correction_of_trade_id == original.trade_id
    position = repository.list_positions()[0]
    assert position.quantity == 0
    assert position.effective_trade_count == 0
    assert position.source_trade_ids == [original.trade_id, reversal.trade_id]


def test_manual_trade_does_not_change_paper_account(manual_context) -> None:
    _, manual_service = manual_context
    simulation = SimulationService(audit_store=TradingAuditStore())
    before = simulation.paper_account()

    _confirm(manual_service, _trade())

    assert simulation.paper_account() == before


def test_paper_trade_does_not_change_manual_positions(manual_context) -> None:
    repository, manual_service = manual_context
    _confirm(manual_service, _trade())
    before = repository.list_positions()
    simulation = SimulationService(audit_store=TradingAuditStore())
    result = simulation.submit_paper_order(
        OrderIntent(
            symbol="600000.SH",
            side=OrderSide.BUY,
            quantity=100,
            reference_price=10,
            price_time=datetime.now().astimezone(),
            approved_by="paper-approver",
        ),
        RiskLimits(),
    )

    assert result.status.value == "filled"
    assert repository.list_positions() == before


def test_service_dependency_boundaries_exclude_manual_writes() -> None:
    decision_source = inspect.getsource(DecisionService)
    simulation_source = inspect.getsource(SimulationService)
    manual_source = inspect.getsource(ManualTradeService)

    assert "ManualTradeRepository" not in decision_source
    assert "manual_trade" not in decision_source.lower()
    assert "ManualTradeRepository" not in simulation_source
    assert "manual_trade" not in simulation_source.lower()
    assert "SimulationService" not in manual_source
    assert "SimulationRepository" not in manual_source
    assert "PaperTradingEngine" not in manual_source


def test_manual_trade_history_has_no_update_or_delete_path() -> None:
    source = inspect.getsource(ManualTradeRepository).lower()
    assert "update manual_trades" not in source
    assert "delete from manual_trades" not in source
    assert "broker_verified" not in source
    assert "auto_synced" not in source


def test_scheduled_tasks_cannot_confirm_manual_trades() -> None:
    project_root = Path(__file__).resolve().parents[1]
    scheduled_sources = [
        project_root / "scripts" / "generate_daily_review.py",
        project_root / "scripts" / "configure_hermes_integration.py",
    ]
    for path in scheduled_sources:
        source = path.read_text(encoding="utf-8")
        assert "confirm_preview" not in source
        assert "/manual-trades/previews/" not in source


def test_chat_natural_language_only_creates_preview(manual_context) -> None:
    _, service = manual_context
    chat = ManualTradeChatService(service)
    reply = chat.preview_natural_language_trade(
        message="我刚刚在外部客户端买入了浦发银行。",
        normalized_trade=_trade(),
        authenticated_user="user-1",
        channel="wecom",
    )

    assert reply.preview is not None
    assert f"确认录入 {reply.preview.confirmation_id}" in reply.text
    assert _manual_trade_count() == 0
    with pytest.raises(ValueError, match="must exactly match"):
        chat.confirm_explicit_message(
            message=f"请确认录入 {reply.preview.confirmation_id}",
            authenticated_user="user-1",
            channel="wecom",
        )
    assert _manual_trade_count() == 0

    confirmation = chat.confirm_explicit_message(
        message=f"确认录入 {reply.preview.confirmation_id}",
        authenticated_user="user-1",
        channel="wecom",
    )
    assert confirmation.confirmation is not None
    assert _manual_trade_count() == 1


def test_manual_trade_http_flow_requires_creator_confirmation(
    manual_context,
    monkeypatch,
) -> None:
    _, service = manual_context
    monkeypatch.setattr(trading_routes, "manual_trade_service", service)
    client = TestClient(app)
    payload = {
        "trade": _trade(client_trade_id="http-trade-1").model_dump(mode="json"),
        "expires_in_seconds": 600,
    }
    creator_headers = {
        "X-Authenticated-User": "user-1",
        "X-Authenticated-Channel": "qq",
    }
    preview_response = client.post(
        "/v1/manual-trades/previews",
        json=payload,
        headers=creator_headers,
    )
    assert preview_response.status_code == 200
    confirmation_id = preview_response.json()["confirmation_id"]
    assert _manual_trade_count() == 0

    forbidden = client.post(
        f"/v1/manual-trades/previews/{confirmation_id}/confirm",
        headers={
            "X-Authenticated-User": "user-2",
            "X-Authenticated-Channel": "qq",
        },
    )
    assert forbidden.status_code == 403
    confirmed = client.post(
        f"/v1/manual-trades/previews/{confirmation_id}/confirm",
        headers=creator_headers,
    )
    repeated = client.post(
        f"/v1/manual-trades/previews/{confirmation_id}/confirm",
        headers=creator_headers,
    )
    assert confirmed.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["idempotent_replay"] is True
    assert repeated.json()["trade"]["trade_id"] == confirmed.json()["trade"]["trade_id"]
    assert _manual_trade_count() == 1
