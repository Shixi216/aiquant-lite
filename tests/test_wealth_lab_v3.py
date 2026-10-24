"""Wealth Lab v3 多资产研究池测试（独立临时库，无网络）。"""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
import pytest

from trading.wealth_lab.config import WEALTH_LAB_DB_PATH
from trading.wealth_lab.universe import (
    SURVIVORSHIP_NOTE,
    backtest_eligible,
    list_universes,
    register_universe,
    universe_members,
    upsert_assets,
)


@pytest.fixture()
def v3_database(tmp_path, monkeypatch):
    import trading.wealth_lab.universe as universe_mod
    import trading.wealth_lab.repository as repo_mod
    path = tmp_path / "wl_v3.duckdb"
    monkeypatch.setattr(universe_mod, "WEALTH_LAB_DB_PATH", path)
    monkeypatch.setattr(repo_mod, "WEALTH_LAB_DB_PATH", path)
    # 行情：资产 A 全程、B 2022 上市、C 流动性极低
    idx = pd.bdate_range("2020-01-02", periods=900)
    con = duckdb.connect(str(path))
    con.execute(repo_mod._SCHEMA)
    rows = []
    for symbol, start in [("AAA.SH", 0), ("BBB.SZ", 500), ("CCC.SH", 0)]:
        for i in range(start, len(idx)):
            rows.append([symbol, idx[i].date(), 1.0, 1.0, 1.0, 10.0, 100.0,
                         1e4 if symbol == "CCC.SH" else 5e6, 1.0, 10.0, "OFFICIAL", "test", None])
    con.executemany("INSERT INTO etf_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.close()
    upsert_assets([
        {"symbol": "AAA.SH", "name": "A", "category": "CN_EQUITY_BROAD_ETF", "exchange": "SSE",
         "list_date": date(2019, 1, 1), "valid_days": 900, "adj_missing": 0,
         "avg_amount_20d_cny": 5e7, "source_breakdown": {"OFFICIAL": 900},
         "quality_status": "QUALIFIED", "data_first": date(2020, 1, 2), "data_last": date(2023, 6, 1)},
        {"symbol": "BBB.SZ", "name": "B", "category": "CN_BOND_ETF", "exchange": "SZSE",
         "list_date": date(2021, 12, 1), "valid_days": 400, "adj_missing": 0,
         "avg_amount_20d_cny": 5e7, "source_breakdown": {"OFFICIAL": 400},
         "quality_status": "QUALIFIED", "data_first": date(2021, 12, 2), "data_last": date(2023, 6, 1)},
        {"symbol": "CCC.SH", "name": "C", "category": "CN_REIT", "exchange": "SSE",
         "list_date": date(2019, 1, 1), "valid_days": 900, "adj_missing": 0,
         "avg_amount_20d_cny": 1e7, "source_breakdown": {"OFFICIAL": 900},
         "quality_status": "DEGRADED", "data_first": date(2020, 1, 2), "data_last": date(2023, 6, 1)},
    ])
    register_universe("test-pool", "1.0", "测试池", "单元测试", ["AAA.SH", "BBB.SZ", "CCC.SH"])
    yield path


def test_universe_registration_and_members(v3_database):
    universes = list_universes()
    pool = next(u for u in universes if u["universe_id"] == "test-pool")
    assert pool["members"] == 3
    assert "幸存者偏差" in pool["survivorship_bias_note"]
    assert sorted(universe_members("test-pool")) == ["AAA.SH", "BBB.SZ", "CCC.SH"]


def test_pit_eligibility_rules(v3_database):
    # 2020 年中：B 未上市、C 流动性不足 → 仅 A 合格
    mid2020 = backtest_eligible(["AAA.SH", "BBB.SZ", "CCC.SH"], date(2021, 6, 30))
    assert mid2020["AAA.SH"]["eligible"] is True
    assert mid2020["BBB.SZ"]["eligible"] is False
    assert any("上市" in r for r in mid2020["BBB.SZ"]["reasons"])
    assert any("流动性" in r for r in mid2020["CCC.SH"]["reasons"])
    # 2023 年：B 已有 ~370 天历史（<250 不满足？500..900 段=~400 交易日 → 合格）
    late = backtest_eligible(["AAA.SH", "BBB.SZ"], date(2023, 5, 30))
    assert late["BBB.SZ"]["eligible"] is True


def test_quality_status_and_sources(v3_database):
    from trading.wealth_lab.universe import assets_report
    rows = {a["symbol"]: a for a in assets_report(["AAA.SH", "CCC.SH"])}
    assert rows["AAA.SH"]["quality_status"] == "QUALIFIED"
    assert rows["CCC.SH"]["quality_status"] == "DEGRADED"
    assert rows["AAA.SH"]["source_breakdown"] == {"OFFICIAL": 900}


def test_survivorship_note_constant():
    assert "幸存者偏差" in SURVIVORSHIP_NOTE or "存续" in SURVIVORSHIP_NOTE
