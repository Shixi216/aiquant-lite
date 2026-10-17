from __future__ import annotations

from datetime import datetime, timezone

import duckdb
from fastapi.testclient import TestClient

from config.settings import settings
from database.migrations.v0123_cross_section_thresholds import (
    apply_migration as apply_v0123,
)
from router.api.app import app
from trading.decision_support.formal_score_resolver import FormalScoreResolver

client = TestClient(app)


def _prepare(tmp_path, monkeypatch) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "cross-section.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    connection = duckdb.connect(str(db_path))
    apply_v0123(connection)
    # v0123 only creates thresholds/history; the main table is created by
    # compute_cross_section_snapshot.py with this exact DDL.
    connection.execute(
        """CREATE TABLE IF NOT EXISTS cross_section_factor_snapshot(
            snapshot_id VARCHAR, symbol VARCHAR, data_cutoff TIMESTAMP WITH TIME ZONE,
            tech_rank DOUBLE, fund_rank DOUBLE, sent_rank DOUBLE, margin_rank DOUBLE,
            composite_score DOUBLE, generated_at TIMESTAMP WITH TIME ZONE,
            PRIMARY KEY(snapshot_id, symbol))"""
    )
    connection.execute("""CREATE TABLE stock_universe(
        symbol VARCHAR, short_name VARCHAR, company_name VARCHAR, updated_at TIMESTAMPTZ)""")
    connection.execute("""CREATE TABLE stock_industry_memberships(
        symbol VARCHAR, industry_name VARCHAR, valid_to TIMESTAMPTZ, generated_at TIMESTAMPTZ)""")
    return connection


def _seed(connection: duckdb.DuckDBPyConnection) -> None:
    now = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)
    rows = [
        ("snap-1", "600519.SH", cutoff, 0.95, 0.80, 0.70, 0.60, 0.85, now),
        ("snap-1", "000001.SZ", cutoff, 0.40, 0.50, 0.60, 0.70, 0.52, now),
        ("snap-1", "300750.SZ", cutoff, 0.90, 0.30, 0.20, 0.10, 0.55, now),
        ("snap-1", "600177.SH", cutoff, 0.10, 0.20, 0.30, 0.40, 0.22, now),
        # excluded from composite sort but counted? composite NULL -> excluded
        ("snap-1", "000012.SZ", cutoff, 0.50, 0.50, 0.50, 0.50, None, now),
    ]
    connection.executemany(
        "INSERT INTO cross_section_factor_snapshot VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    connection.execute(
        "INSERT INTO cross_section_thresholds VALUES (?,?,?,?,?,?,?,?,?)",
        [cutoff, 0.40, 0.55, 0.70, 0.85, 0.70, 0.55, 4, now],
    )
    connection.execute(
        "INSERT INTO stock_universe VALUES ('600519.SH','贵州茅台',NULL,?), ('000001.SZ','平安银行',NULL,?)",
        [now, now],
    )
    connection.execute(
        "INSERT INTO stock_industry_memberships VALUES ('600519.SH','白酒',NULL,?), ('000001.SZ','银行',NULL,?)",
        [now, now],
    )


def test_cross_section_latest_sort_filter_thresholds(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    _seed(connection)
    connection.close()

    response = client.get(
        "/v1/cross-section/latest",
        params={"limit": 2, "offset": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4  # NULL composite excluded
    assert [item["symbol"] for item in body["items"]] == ["600519.SH", "300750.SZ"]
    assert body["items"][0]["composite_score"] == 0.85
    assert body["items"][0]["name"] == "贵州茅台"
    assert body["items"][0]["industry"] == "白酒"
    assert body["thresholds"] is not None
    assert body["thresholds"]["strong_buy_q"] == 0.70
    assert body["thresholds"]["n_total"] == 4

    # offset
    paged = client.get(
        "/v1/cross-section/latest", params={"limit": 2, "offset": 2}
    ).json()
    assert [item["symbol"] for item in paged["items"]] == ["000001.SZ", "600177.SH"]

    # min_composite filter (0.85 is the only composite >= 0.6)
    filtered = client.get(
        "/v1/cross-section/latest", params={"min_composite": 0.6}
    ).json()
    assert filtered["total"] == 1
    assert [item["symbol"] for item in filtered["items"]] == ["600519.SH"]

    # alternative sort keys
    by_tech = client.get("/v1/cross-section/latest", params={"sort": "tech"}).json()
    assert by_tech["items"][0]["symbol"] == "600519.SH"
    by_margin = client.get(
        "/v1/cross-section/latest", params={"sort": "margin"}
    ).json()
    assert by_margin["items"][0]["symbol"] == "000001.SZ"


def test_cross_section_symbol_matches_resolver(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    _seed(connection)
    connection.close()

    response = client.get("/v1/cross-section/600519.SH")
    assert response.status_code == 200
    body = response.json()
    assert body["score_source"] == "cross_section_4d"
    assert body["composite_score"] == 0.85
    assert body["tech_rank"] == 0.95

    resolved = FormalScoreResolver().read_cross_section(
        "600519.SH", with_detail=True
    )
    assert resolved is not None
    score, detail = resolved
    assert score == body["composite_score"]
    assert detail["tech_rank"] == body["tech_rank"]

    # bare code resolves to the same record
    bare = client.get("/v1/cross-section/600519")
    assert bare.status_code == 200
    assert bare.json()["composite_score"] == 0.85

    unknown = client.get("/v1/cross-section/688111.SH")
    assert unknown.status_code == 404


def test_cross_section_latest_empty_table_404(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    response = client.get("/v1/cross-section/latest")
    assert response.status_code == 404


def test_negative_composite_filter_uses_centered_score_scale(tmp_path, monkeypatch):
    connection = _prepare(tmp_path, monkeypatch)
    _seed(connection)
    connection.execute("UPDATE cross_section_factor_snapshot SET composite_score=-0.25 WHERE symbol='600177.SH'")
    connection.close()
    response = client.get("/v1/cross-section/latest", params={"min_composite": -0.5})
    assert response.status_code == 200
    assert "600177.SH" in [item["symbol"] for item in response.json()["items"]]
    response = client.get("/v1/cross-section/latest", params={"min_composite": 0})
    assert "600177.SH" not in [item["symbol"] for item in response.json()["items"]]
