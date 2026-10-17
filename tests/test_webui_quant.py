from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest
from fastapi.testclient import TestClient

from webui import config as webui_config
from webui.app import app
import webui.quant.reader as reader

client = TestClient(app)


# ---------------------------------------------------------------------------
# 夹具：构造迷你 replay 侧车库（与真实表同名同关键列）
# ---------------------------------------------------------------------------

DAYS = 80
START = date(2026, 4, 1)


def _iso(offset: int) -> str:
    return (START + timedelta(days=offset)).isoformat()


def _install_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE replay_stock_base (
            symbol VARCHAR, trade_date DATE, trade_index BIGINT,
            close DOUBLE, volume DOUBLE, amount DOUBLE,
            return_pct DOUBLE, volume_ratio DOUBLE, turnover_rate DOUBLE,
            adj_factor DOUBLE, is_suspended BOOLEAN,
            is_limit_up BOOLEAN, is_limit_down BOOLEAN, is_broken_board BOOLEAN
        )
        """
    )
    con.execute("CREATE TABLE replay_calendar_index (trade_date DATE, trade_index BIGINT)")
    con.execute(
        """
        CREATE TABLE replay_market_snapshots (
            trade_date DATE, valid_quote_count BIGINT, advancers BIGINT,
            decliners BIGINT, unchanged BIGINT, market_return DOUBLE,
            market_turnover DOUBLE, advance_ratio DOUBLE, decline_ratio DOUBLE,
            advance_decline_ratio DOUBLE, breadth_strength DOUBLE,
            market_turnover_change DOUBLE
        )
        """
    )
    con.execute(
        "CREATE TABLE replay_margin_market (trade_date DATE, market_margin_total DOUBLE)"
    )
    con.execute(
        """
        CREATE TABLE replay_dragon_tiger_events (
            trade_date DATE, symbol VARCHAR, reasons_json VARCHAR,
            buy_amount DOUBLE, sell_amount DOUBLE, net_amount DOUBLE,
            institution_net_amount DOUBLE, institution_buy_amount DOUBLE,
            institution_sell_amount DOUBLE, seat_count BIGINT,
            institution_seat_count BIGINT
        )
        """
    )
    con.execute("CREATE TABLE replay_security_master (symbol VARCHAR, name VARCHAR)")


@pytest.fixture()
def mini_replay(tmp_path, monkeypatch):
    db_path = tmp_path / "mini_replay.duckdb"
    con = duckdb.connect(str(db_path))
    _install_schema(con)

    calendar_rows = [( _iso(i), i + 1) for i in range(DAYS)]
    con.executemany(
        "INSERT INTO replay_calendar_index VALUES (?, ?)", calendar_rows
    )

    # A: 持续上行（均线多头），最后 3 天连续涨停
    rows_a = []
    for i in range(DAYS):
        close = 10.0 * (1.01 ** i)
        is_limit = i >= DAYS - 3
        rows_a.append(
            ("600001.SH", _iso(i), i + 1, close, 1000.0, close * 1000,
             0.01 if i else 0.0, 1.0, 0.02, 1.0, False,
             is_limit, False, False)
        )
    # B: 横盘，末日放量上涨（volume_surge）
    rows_b = []
    for i in range(DAYS):
        last = i == DAYS - 1
        rows_b.append(
            ("000002.SZ", _iso(i), i + 1, 10.0, 2000.0, 20000.0,
             0.02 if last else 0.0, 3.0 if last else 1.0, 0.01, 1.0, False,
             False, False, False)
        )
    # C: 下跌后单日大幅反弹（金叉：18 元上穿 ma20）
    rows_c = []
    for i in range(DAYS):
        close = 20.0 - (10.0 * i / (DAYS - 2)) if i < DAYS - 1 else 18.0
        rows_c.append(
            ("300003.SZ", _iso(i), i + 1, close, 1500.0, close * 1500,
             (0.80 if i == DAYS - 1 else -0.01), 1.5, 0.03, 1.0, False,
             False, False, False)
        )
    con.executemany(
        "INSERT INTO replay_stock_base VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows_a + rows_b + rows_c,
    )
    monkeypatch.setattr(reader, "_DENSE_MIN_SYMBOLS", 1)
    last_iso = _iso(DAYS - 1)
    con.execute(
        """
        INSERT INTO replay_market_snapshots VALUES
        (?, 3, 2, 1, 0, 0.0056, 30000.0, 0.667, 0.333, 2.0, 0.8, 0.03)
        """,
        [last_iso],
    )
    con.executemany(
        "INSERT INTO replay_margin_market VALUES (?, ?)",
        [(_iso(DAYS - 2), 1_000_000.0), (last_iso, 1_010_000.0)],
    )
    con.execute(
        """
        INSERT INTO replay_dragon_tiger_events VALUES
        (?, '600001.SH', '["日涨幅偏离值达7%的证券","日换手率达20%的证券"]',
         5.0e7, 1.0e7, 4.0e7, 2.0e7, 1.0e7, 0.0, 5, 2),
        (?, '000002.SZ', '["日振幅值达15%的证券"]',
         1.0e7, 3.0e7, -2.0e7, 0.0, 0.0, 0.0, 3, 0)
        """,
        [last_iso, last_iso],
    )
    con.executemany(
        "INSERT INTO replay_security_master VALUES (?, ?)",
        [("600001.SH", "示例一号"), ("000002.SZ", "示例二号"), ("300003.SZ", "示例三号")],
    )
    con.close()

    monkeypatch.setattr(reader, "_database_path", lambda: db_path)
    reader._DENSE_CACHE.clear()
    yield db_path
    reader._DENSE_CACHE.clear()


# ---------------------------------------------------------------------------
# reader 单元
# ---------------------------------------------------------------------------

def test_dense_latest_date_picks_actual_dense_day(mini_replay) -> None:
    as_of, count = reader.dense_latest_date()
    assert as_of == _iso(DAYS - 1)
    assert count == 3


def test_market_overview_counts(mini_replay) -> None:
    overview = reader.market_overview(_iso(DAYS - 1))
    assert overview["advancers"] == 2
    assert overview["decliners"] == 1
    assert overview["limit_ups"] == 1  # A 连板末日
    assert overview["market_return"] == pytest.approx(0.56)  # 已归一化为百分数


def test_consecutive_boards_streak(mini_replay) -> None:
    boards = reader.consecutive_boards(_iso(DAYS - 1), min_streak=2)
    assert boards[0]["symbol"] == "600001.SH"
    assert boards[0]["streak"] == 3
    assert boards[0]["name"] == "示例一号"


def test_scan_ma_bull_and_volume_surge(mini_replay) -> None:
    bull = reader.scan("ma_bull", _iso(DAYS - 1), 10)
    assert "600001.SH" in [item["symbol"] for item in bull["items"]]

    surge = reader.scan("volume_surge", _iso(DAYS - 1), 10)
    assert "000002.SZ" in [item["symbol"] for item in surge["items"]]
    item = next(i for i in surge["items"] if i["symbol"] == "000002.SZ")
    assert item["return_pct"] == pytest.approx(2.0)  # 百分数
    assert item["volume_ratio"] == pytest.approx(3.0)


def test_scan_golden_cross_after_v_reversal(mini_replay) -> None:
    cross = reader.scan("golden_cross", _iso(DAYS - 1), 10)
    assert "300003.SZ" in [item["symbol"] for item in cross["items"]]


def test_scan_unknown_type_raises(mini_replay) -> None:
    with pytest.raises(ValueError):
        reader.scan("nope", _iso(DAYS - 1), 5)


def test_rps_leaders_percentile(mini_replay) -> None:
    result = reader.rps_leaders(_iso(DAYS - 1), days=60, min_rps=50.0, limit=10)
    symbols = {item["symbol"]: item for item in result["items"]}
    # A 涨幅最大 → RPS 最高
    top = max(symbols.values(), key=lambda item: item["rps"])
    assert top["symbol"] == "600001.SH"


def test_dragon_tiger_parses_reasons(mini_replay) -> None:
    items = reader.dragon_tiger(_iso(DAYS - 1), 10)
    by_symbol = {item["symbol"]: item for item in items}
    assert items[0]["symbol"] == "600001.SH"  # |net| 最大
    assert by_symbol["600001.SH"]["reasons"] == ["日涨幅偏离7%", "日换手20%"]
    assert by_symbol["000002.SZ"]["net_amount"] < 0


def test_breadth_and_margin_history(mini_replay) -> None:
    breadth = reader.breadth_history(10)
    assert breadth[-1]["trade_date"] == _iso(DAYS - 1)
    assert breadth[-1]["limit_ups"] == 1
    margin = reader.margin_history(10)
    assert margin[-1]["change_pct"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

def test_quant_routes_live(mini_replay) -> None:
    overview = client.get("/webui/v2/quant/overview").json()
    assert overview["available"] is True
    assert overview["data_cutoff"] == _iso(DAYS - 1)
    assert overview["data"]["sentiment"]["limit_ups"] == 1
    assert 0.0 <= overview["data"]["temperature"] <= 100.0

    scan = client.get("/webui/v2/quant/scan", params={"type": "ma_bull"}).json()
    assert scan["available"] is True
    assert any(i["symbol"] == "600001.SH" for i in scan["data"]["items"])

    dragon = client.get("/webui/v2/quant/dragon-tiger").json()
    assert dragon["available"] is True
    assert dragon["data"][0]["reasons"]

    breadth = client.get("/webui/v2/quant/breadth").json()
    assert breadth["available"] is True and breadth["data"]

    margin = client.get("/webui/v2/quant/margin").json()
    assert margin["available"] is True

    pools = client.get("/webui/v2/quant/limit-pools").json()
    assert pools["available"] is True
    assert pools["data"]["limit_up_pool"][0]["symbol"] == "600001.SH"

    rps = client.get("/webui/v2/quant/rps", params={"min_rps": 50}).json()
    assert rps["available"] is True

    bad = client.get("/webui/v2/quant/scan", params={"type": "nope"}).json()
    assert bad["available"] is False
    assert bad["reason_code"] == "unknown_scan_type"


def test_quant_routes_get_only(mini_replay) -> None:
    for path in [
        "/webui/v2/quant/overview",
        "/webui/v2/quant/breadth",
        "/webui/v2/quant/scan",
        "/webui/v2/quant/latest",
    ]:
        response = client.post(path)
        assert response.status_code == 405, path


def test_latest_session_failure_degrades(monkeypatch) -> None:
    monkeypatch.setattr(
        reader, "_latest_session_sync", lambda today: (_ for _ in ()).throw(RuntimeError("x"))
    )
    reader._SNAPSHOT_CACHE.clear()
    body = client.get("/webui/v2/quant/latest").json()
    reader._SNAPSHOT_CACHE.clear()
    assert body["available"] is False
    assert body["reason_code"] == "latest_session_unavailable"


def test_quant_gate_still_forbidden_pattern_free() -> None:
    import re
    from pathlib import Path

    forbidden = [
        r"from\s+database\.db\s+import", r"database\.db", r"get_connection\(",
        r"initialize_database\(", r"opc_database_path", r"open_database\(",
        r"run_migrations", r"CREATE\s+TABLE", r"INSERT\s+INTO",
        r"UPDATE\s+\w+\s+SET", r"DELETE\s+FROM", r"DROP\s+TABLE",
    ]
    root = Path(webui_config.PROJECT_ROOT) / "webui" / "quant"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not re.search(pattern, text), (path.name, pattern)
