from __future__ import annotations

from datetime import datetime, timezone

import duckdb

from config.settings import settings
from trading.research.equity_context import EquityContextReader


def test_database_backed_market_sector_and_stock_context(tmp_path, monkeypatch) -> None:
    path = tmp_path / "context.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE market_snapshot_runs(
        snapshot_id VARCHAR, snapshot_time TIMESTAMPTZ, coverage_ratio DOUBLE,
        completeness_status VARCHAR, mode VARCHAR)""")
    con.execute("""CREATE TABLE market_snapshot_items(
        snapshot_id VARCHAR, symbol VARCHAR, price DOUBLE, previous_close DOUBLE,
        open DOUBLE, high DOUBLE, low DOUBLE, volume DOUBLE, amount DOUBLE,
        change_pct DOUBLE, turnover_rate DOUBLE, snapshot_time TIMESTAMPTZ,
        source VARCHAR, is_suspended BOOLEAN, is_abnormal BOOLEAN, payload_json JSON)""")
    con.execute("""CREATE TABLE stock_industry_memberships(
        symbol VARCHAR, industry_code VARCHAR, industry_name VARCHAR,
        industry_level VARCHAR, classification_system VARCHAR,
        valid_to TIMESTAMPTZ, source VARCHAR, verification_status VARCHAR,
        generated_at TIMESTAMPTZ)""")
    con.execute("""CREATE TABLE cross_section_factor_snapshot(
        symbol VARCHAR, composite_score DOUBLE)""")
    con.execute("""CREATE TABLE stock_universe(
        symbol VARCHAR, short_name VARCHAR, company_name VARCHAR, exchange VARCHAR,
        market VARCHAR, board VARCHAR, list_date DATE, listing_status VARCHAR,
        is_st BOOLEAN, is_suspended BOOLEAN, primary_source VARCHAR,
        verification_status VARCHAR, updated_at TIMESTAMPTZ)""")
    con.execute("""CREATE TABLE sentiment_v3_snapshots(
        snapshot_id VARCHAR, snapshot_type VARCHAR, trade_date DATE,
        symbol_scope VARCHAR, source VARCHAR, available_time TIMESTAMPTZ,
        record_source VARCHAR, revision INTEGER, created_at TIMESTAMPTZ,
        quality_flags_json JSON, payload_json JSON)""")
    now = datetime(2026, 9, 4, 15, tzinfo=timezone.utc)
    con.execute("INSERT INTO market_snapshot_runs VALUES ('s1', ?, 1.0, 'COMPLETE', 'APPLY')", [now])
    for index, (symbol, pct) in enumerate((("600001.SH", 2.0), ("600002.SH", 1.0), ("600003.SH", -1.0))):
        con.execute("INSERT INTO market_snapshot_items VALUES ('s1', ?, 10, 9.8, 9.9, 10.2, 9.7, 1000, ?, ?, 1.2, ?, 'test', false, false, '{\"amount_unit\":\"CNY\"}')", [symbol, 10000 + index, pct, now])
        con.execute("INSERT INTO stock_industry_memberships VALUES (?, 'I1', '测试行业', 'L1', 'TEST', NULL, 'test', 'VERIFIED', ?)", [symbol, now])
        con.execute("INSERT INTO cross_section_factor_snapshot VALUES (?, ?)", [symbol, 0.1 + index / 10])
    con.execute("INSERT INTO stock_universe VALUES ('600001.SH','测试股份','测试股份有限公司','SH','A_SHARE','SH_MAIN','2000-01-01','ACTIVE',false,false,'test','VERIFIED',?)", [now])
    con.execute("""INSERT INTO sentiment_v3_snapshots VALUES(
        'dt1','DRAGON_TIGER','2026-09-04','600001.SH','test',?,
        'REAL',0,?,'[\"EOD_ONLY\"]',
        '{"has_dragon_tiger":true,"reasons":["测试原因"],"buy_amount":200,"sell_amount":100,"net_amount":100,"institution_net_amount":50,"seat_count":10,"institution_seat_count":2}')""", [now, now])
    con.close()

    reader = EquityContextReader()
    overview = reader.overview()
    assert overview["valid_symbol_count"] == 3
    assert overview["sectors"][0]["sector"] == "测试行业"
    assert overview["sectors"][0]["advances"] == 2
    stock = reader.stock("600001.SH")
    assert stock["identity"]["name"] == "测试股份"
    assert stock["industry"]["name"] == "测试行业"
    assert stock["quote"]["change_pct"] == 2.0
    assert reader.search_stocks("测试股份")[0]["symbol"] == "600001.SH"
    assert reader.search_stocks("600001")[0]["name"] == "测试股份"
    assert reader.search_stocks("不存在") == []
    dragon = reader.dragon_tiger_board()
    assert dragon["is_realtime"] is False
    assert dragon["items"][0]["name"] == "测试股份"
    assert dragon["items"][0]["net_amount"] == 100
