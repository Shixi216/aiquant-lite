"""v3 中国多资产研究池：资产主数据、质量审计与双 Universe 管理。

复用既有 etf_daily 行情表（含 data_source 审计）与双源 Provider；
本模块只新增管理表与 PIT 判定，不重复建设行情存储。
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone

import duckdb

from trading.wealth_lab.config import WEALTH_LAB_DB_PATH

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.Lock()

UNIVERSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS wealth_assets (
    symbol VARCHAR PRIMARY KEY,
    name VARCHAR,
    category VARCHAR NOT NULL,
    exchange VARCHAR,
    list_date DATE,
    delist_date DATE,
    data_first DATE,
    data_last DATE,
    valid_days INTEGER,
    expected_days INTEGER,
    missing_ratio DOUBLE,
    adj_missing INTEGER,
    avg_amount_20d_cny DOUBLE,
    source_breakdown VARCHAR,
    quality_status VARCHAR,
    notes VARCHAR,
    product_form VARCHAR,
    underlying VARCHAR,
    market_scope VARCHAR,
    currency_risk VARCHAR,
    updated_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS wealth_universes (
    universe_id VARCHAR PRIMARY KEY,
    universe_version VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    description VARCHAR,
    survivorship_bias_note VARCHAR,
    created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS wealth_universe_members (
    universe_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    added_at TIMESTAMPTZ,
    PRIMARY KEY (universe_id, symbol)
);
"""

SURVIVORSHIP_NOTE = (
    "候选池基于当前存续产品名录构建，未包含已退市/清盘的历史产品；"
    "2020 年时点的完整市场代表性未经历史全量名录核验，幸存者偏差未被消除，仅被如实记录。"
)


def _serialize_breakdown(value) -> str:
    """dict -> JSON 字符串；已是合法 JSON 字符串则原样（避免双重序列化）。"""
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except Exception:
            return "{}"
    return json.dumps(value or {})


def _connect():
    con = duckdb.connect(str(WEALTH_LAB_DB_PATH))
    con.execute(UNIVERSE_SCHEMA)
    # v3.1 维度列（旧表幂等补列）
    for column, default in (("product_form", "'ETF'"), ("underlying", "''"),
                            ("market_scope", "'DOMESTIC'"), ("currency_risk", "'NONE'")):
        try:
            con.execute(f"ALTER TABLE wealth_assets ADD COLUMN {column} VARCHAR DEFAULT {default}")
        except duckdb.Error:
            pass  # 列已存在
    return con


def upsert_assets(rows: list[dict]) -> None:
    with _LOCK, _connect() as con:
        for row in rows:
            con.execute(
                """INSERT INTO wealth_assets
                   (symbol, name, category, exchange, list_date, delist_date,
                    data_first, data_last, valid_days, expected_days, missing_ratio,
                    adj_missing, avg_amount_20d_cny, source_breakdown, quality_status,
                    notes, product_form, underlying, market_scope, currency_risk, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (symbol) DO UPDATE SET
                     name=excluded.name, category=excluded.category, exchange=excluded.exchange,
                     list_date=excluded.list_date, delist_date=excluded.delist_date,
                     data_first=excluded.data_first, data_last=excluded.data_last,
                     valid_days=excluded.valid_days, expected_days=excluded.expected_days,
                     missing_ratio=excluded.missing_ratio, adj_missing=excluded.adj_missing,
                     avg_amount_20d_cny=excluded.avg_amount_20d_cny,
                     source_breakdown=excluded.source_breakdown,
                     quality_status=excluded.quality_status, notes=excluded.notes,
                     product_form=excluded.product_form, underlying=excluded.underlying,
                     market_scope=excluded.market_scope, currency_risk=excluded.currency_risk,
                     updated_at=excluded.updated_at""",
                [
                    row["symbol"], row["name"], row["category"], row.get("exchange"),
                    row.get("list_date"), row.get("delist_date"),
                    row.get("data_first"), row.get("data_last"),
                    row.get("valid_days", 0), row.get("expected_days", 0),
                    row.get("missing_ratio", 1.0), row.get("adj_missing", 0),
                    row.get("avg_amount_20d_cny"), _serialize_breakdown(row.get("source_breakdown")),
                    row.get("quality_status", "REJECTED"), row.get("notes", ""),
                    row.get("product_form", "ETF"), row.get("underlying", ""),
                    row.get("market_scope", "DOMESTIC"), row.get("currency_risk", "NONE"),
                    datetime.now(_TZ),
                ],
            )


def register_universe(universe_id: str, version: str, name: str, description: str,
                      symbols: list[str]) -> dict:
    with _LOCK, _connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO wealth_universes VALUES (?,?,?,?,?,?)",
            [universe_id, version, name, description, SURVIVORSHIP_NOTE, datetime.now(_TZ)],
        )
        con.execute("DELETE FROM wealth_universe_members WHERE universe_id=?", [universe_id])
        for symbol in symbols:
            con.execute("INSERT INTO wealth_universe_members VALUES (?,?,?)",
                        [universe_id, symbol, datetime.now(_TZ)])
    return {"universe_id": universe_id, "universe_version": version, "members": len(symbols)}


def list_universes() -> list[dict]:
    with _LOCK, _connect() as con:
        rows = con.execute(
            """SELECT u.universe_id, u.universe_version, u.name, u.description,
                      u.survivorship_bias_note, u.created_at, COUNT(m.symbol)
               FROM wealth_universes u LEFT JOIN wealth_universe_members m
               USING (universe_id) GROUP BY ALL ORDER BY u.created_at"""
        ).fetchall()
    return [
        {"universe_id": r[0], "universe_version": r[1], "name": r[2], "description": r[3],
         "survivorship_bias_note": r[4], "created_at": r[5].isoformat(), "members": int(r[6])}
        for r in rows
    ]


def universe_members(universe_id: str) -> list[str]:
    with _LOCK, _connect() as con:
        rows = con.execute(
            "SELECT symbol FROM wealth_universe_members WHERE universe_id=? ORDER BY symbol",
            [universe_id],
        ).fetchall()
    return [r[0] for r in rows]


def assets_report(symbols: list[str] | None = None) -> list[dict]:
    with _LOCK, _connect() as con:
        if symbols:
            marks = ",".join("?" for _ in symbols)
            rows = con.execute(
                f"SELECT * FROM wealth_assets WHERE symbol IN ({marks}) ORDER BY symbol", symbols
            ).fetchall()
            cols = [d[0] for d in con.description]
        else:
            rows = con.execute("SELECT * FROM wealth_assets ORDER BY category, symbol").fetchall()
            cols = [d[0] for d in con.description]
    out = []
    for row in rows:
        item = dict(zip(cols, row))
        raw = item.get("source_breakdown")
        for _ in range(3):  # 兼容历史双重编码
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw or "{}")
                except Exception:
                    raw = {}
            else:
                break
        item["source_breakdown"] = raw if isinstance(raw, dict) else {}
        # v3.2: 日期字段统一 ISO 字符串（历史行为保持）
        for key in ("list_date", "delist_date", "data_first", "data_last"):
            if item.get(key) is not None:
                item[key] = item[key].isoformat()
        out.append(item)
    return out


def backtest_eligible(
    symbols: list[str],
    asof: date,
    *,
    min_history_days: int = 250,
    min_avg_amount_cny: float = 20_000_000.0,
) -> dict[str, dict]:
    """PIT 判定：某决策时点各资产是否可入回测（只用时点可知信息）。"""
    with _LOCK, _connect() as con:
        result = {}
        for symbol in symbols:
            row = con.execute(
                "SELECT list_date, delist_date, data_first FROM wealth_assets WHERE symbol=?",
                [symbol],
            ).fetchone()
            stats = con.execute(
                """SELECT COUNT(*), MIN(trade_date)
                   FROM etf_daily WHERE symbol=? AND trade_date <= ?""",
                [symbol, asof],
            ).fetchone()
            # 流动性：最近 20 个有效交易日均额（修复全量AVG放行枯竭资产）
            recent_avg = con.execute(
                """SELECT CASE WHEN COUNT(*) > 0 THEN SUM(amount)/COUNT(*) END
                   FROM (SELECT amount FROM etf_daily
                         WHERE symbol=? AND trade_date <= ? AND amount IS NOT NULL
                         ORDER BY trade_date DESC LIMIT 20)""",
                [symbol, asof],
            ).fetchone()[0]
            days, first_used = stats
            avg_amount = float(recent_avg or 0.0) * 1000.0  # 千元 → 元
            reasons = []
            if row is None:
                reasons.append("未登记资产")
            else:
                list_date = row[0]
                if list_date is None or list_date > asof:
                    reasons.append(f"尚未上市（上市 {list_date}）")
                if row[1] is not None and row[1] <= asof:
                    reasons.append(f"已退市（{row[1]}）")
            if days < min_history_days:
                reasons.append(f"历史不足（{days}<{min_history_days} 天）")
            if avg_amount < min_avg_amount_cny:
                reasons.append(f"流动性不足（20日均 {avg_amount/1e8:.2f} 亿元口径 {avg_amount:.0f} 元）")
            result[symbol] = {"eligible": not reasons, "reasons": reasons,
                              "history_days_at_asof": int(days or 0)}
    return result
