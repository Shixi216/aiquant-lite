from __future__ import annotations

import duckdb
from fastapi.testclient import TestClient

from config.settings import settings
from router.api.app import app
from trading.decision_support.decision_packet_v2 import (
    create_decision_packet,
    DecisionPacketV2Repository,
)

client = TestClient(app)


def _save_packet(
    repository: DecisionPacketV2Repository,
    *,
    symbol: str,
    entry_zone: list | None = None,
) -> None:
    packet = create_decision_packet(
        local_user_id="user-1",
        symbol=symbol,
        action="BUY",
        formal_score=0.83,
        confidence=0.72,
        reference_price=12.34,
        frozen_entry_zone=entry_zone or [],
        frozen_preferred_zone=[10.9, 11.2] if entry_zone else [],
        frozen_stop_loss_price=9.8 if entry_zone else None,
        data_cutoff="2026-09-04T15:00:00+08:00",
    )
    repository.save(packet)


def test_latest_by_symbol_returns_newest(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "packets.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    repository = DecisionPacketV2Repository()
    _save_packet(repository, symbol="600519.SH")
    # force a later created_at by saving with distinct ids
    import time

    time.sleep(0.01)
    _save_packet(repository, symbol="600519.SH")

    response = client.get("/v1/decision-packets/v2/600519.SH/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "600519.SH"
    assert body["action"] == "BUY"
    assert body["action_zh"] == "建仓"
    assert body["has_frozen_plan"] is False
    assert body["frozen_entry_zone"] is None

    all_rows = repository.list_by_user("user-1", limit=10)
    assert len(all_rows) == 2
    newest_created = max(row.created_at for row in all_rows)
    assert body["created_at"] == newest_created


def test_latest_by_symbol_as_of_filter(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "packets-asof.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    repository = DecisionPacketV2Repository()
    _save_packet(repository, symbol="000001.SZ")
    import time

    time.sleep(0.01)
    _save_packet(repository, symbol="000001.SZ")

    rows = repository.list_by_user("user-1", limit=10)
    created = sorted(row.created_at for row in rows)

    # as_of at/below the first packet only returns that one
    boundary = created[0]
    response = client.get(
        "/v1/decision-packets/v2/000001.SZ/latest",
        params={"as_of": boundary},
    )
    assert response.status_code == 200
    assert response.json()["created_at"] == created[0]


def test_latest_by_symbol_local_user_filter(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "packets-user.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    repository = DecisionPacketV2Repository()
    packet = create_decision_packet(
        local_user_id="user-2",
        symbol="600519.SH",
        action="HOLD",
        formal_score=0.5,
        confidence=0.5,
    )
    repository.save(packet)

    miss = client.get(
        "/v1/decision-packets/v2/600519.SH/latest",
        params={"local_user_id": "user-1"},
    )
    assert miss.status_code == 404

    hit = client.get(
        "/v1/decision-packets/v2/600519.SH/latest",
        params={"local_user_id": "user-2"},
    )
    assert hit.status_code == 200
    assert hit.json()["action"] == "HOLD"


def test_latest_by_symbol_frozen_zones_verbatim(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "packets-zones.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    repository = DecisionPacketV2Repository()
    _save_packet(
        repository,
        symbol="600519.SH",
        entry_zone=[10.5, 11.0],
    )
    connection = duckdb.connect(str(db_path))
    connection.execute(
        "UPDATE decision_packets_v2 SET status='REVOKED' WHERE symbol='600519.SH'"
    )
    connection.close()

    revoked = client.get("/v1/decision-packets/v2/600519.SH/latest")
    assert revoked.status_code == 404


def test_latest_by_symbol_exact_match_no_like(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "packets-exact.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    repository = DecisionPacketV2Repository()
    _save_packet(repository, symbol="600519.SH", entry_zone=[10.5, 11.0])
    _save_packet(repository, symbol="6005190.SZ", entry_zone=[1.0, 2.0])

    response = client.get("/v1/decision-packets/v2/600519.SH/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "600519.SH"
    assert body["frozen_entry_zone"] == {"low": 10.5, "high": 11.0}
    assert body["frozen_preferred_zone"]["low"] == 10.9
    assert body["frozen_stop_loss_price"] == 9.8
    assert body["zone_expired"] is not None

    decoy = client.get("/v1/decision-packets/v2/6005190.SZ/latest")
    assert decoy.status_code == 200
    assert decoy.json()["symbol"] == "6005190.SZ"
    assert decoy.json()["frozen_entry_zone"] == {"low": 1.0, "high": 2.0}

    unknown = client.get("/v1/decision-packets/v2/300750.SZ/latest")
    assert unknown.status_code == 404
    assert "no frozen decision packet" in unknown.json()["detail"]


def test_get_does_not_create_table_on_fresh_database(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "fresh.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)

    response = client.get("/v1/decision-packets/v2/600519.SH/latest")
    assert response.status_code == 404
    assert not db_path.exists()

    connection = duckdb.connect(str(db_path))
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    connection.close()
    assert "decision_packets_v2" not in tables
