"""Wealth Lab 自建数据池（独立 DuckDB：database/wealth_lab.duckdb）。

独立于主库 hermes_opc.duckdb 与 Router 单 Owner 拓扑——不经过
connection_manager，避免与选股系统互相干扰。表结构自管理，幂等写入。

行级审计：etf_daily.data_source 记录该行由哪个数据源拉取（OFFICIAL/PROXY）。
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from trading.wealth_lab.config import SOURCE_NAME, WEALTH_LAB_DB_PATH

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_daily (
    symbol VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    vol DOUBLE, amount DOUBLE,
    adj_factor DOUBLE,
    hfq_close DOUBLE,
    data_source VARCHAR,
    source VARCHAR,
    fetched_at TIMESTAMPTZ,
    PRIMARY KEY (symbol, trade_date)
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    backtest_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    params_json VARCHAR NOT NULL,
    result_json VARCHAR NOT NULL
);
"""


def _connect(path: Path | None = None):
    db_path = Path(path or WEALTH_LAB_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(db_path))
    connection.execute(_SCHEMA)
    return connection


def _num(value) -> float | None:
    return float(value) if value is not None and pd.notna(value) else None


class WealthLabRepository:
    """线程安全的自建池访问层。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    # ---- 原始行情 ----
    def upsert_daily(self, frame: pd.DataFrame, symbol: str) -> int:
        if frame is None or frame.empty:
            return 0
        rows = []
        now = datetime.now()
        for row in frame.to_dict("records"):
            trade_date = str(row["trade_date"])
            rows.append(
                [
                    symbol,
                    date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"),
                    _num(row.get("open")),
                    _num(row.get("high")),
                    _num(row.get("low")),
                    _num(row.get("close")),
                    _num(row.get("vol")),
                    _num(row.get("amount")),
                    _num(row.get("adj_factor")),
                    _num(row.get("hfq_close")),
                    str(row.get("data_source") or PROXY_FALLBACK_LABEL),
                    SOURCE_NAME,
                    now,
                ]
            )
        with _LOCK, _connect(self._path) as con:
            con.executemany(
                """INSERT INTO etf_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (symbol, trade_date) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, vol=excluded.vol, amount=excluded.amount,
                     adj_factor=excluded.adj_factor, hfq_close=excluded.hfq_close,
                     data_source=excluded.data_source, source=excluded.source,
                     fetched_at=excluded.fetched_at""",
                rows,
            )
        return len(rows)

    def read_close_matrix(
        self, symbols: list[str], start: date | None = None, end: date | None = None,
        *, join: str = "inner",
    ) -> pd.DataFrame:
        """共同（inner）或并集（outer）交易日的后复权收盘矩阵。"""
        with _LOCK, _connect(self._path) as con:
            frames = []
            for symbol in symbols:
                query = "SELECT trade_date, hfq_close FROM etf_daily WHERE symbol=? AND hfq_close IS NOT NULL"
                params: list = [symbol]
                if start is not None:
                    query += " AND trade_date >= ?"
                    params.append(start)
                if end is not None:
                    query += " AND trade_date <= ?"
                    params.append(end)
                frame = con.execute(query + " ORDER BY trade_date", params).fetchdf()
                if not frame.empty:
                    frame = frame.set_index("trade_date")
                    frame.columns = [symbol]
                    frames.append(frame[symbol])
        if not frames:
            return pd.DataFrame()
        if join == "outer":
            return pd.concat(frames, axis=1, join="outer").sort_index()
        return pd.concat(frames, axis=1, join="inner").sort_index()

    def pool_status(self) -> dict:
        with _LOCK, _connect(self._path) as con:
            row = con.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM etf_daily"
            ).fetchone()
            breakdown = dict(
                con.execute(
                    "SELECT data_source, COUNT(*) FROM etf_daily GROUP BY 1"
                ).fetchall()
            )
        return {
            "database": str(WEALTH_LAB_DB_PATH),
            "row_count": int(row[0] or 0),
            "first_date": row[1].isoformat() if row[1] else None,
            "last_date": row[2].isoformat() if row[2] else None,
            "source_breakdown": {k: int(v) for k, v in breakdown.items()},
            "sources": [SOURCE_NAME],
        }

    def asset_coverage(self, symbol: str) -> dict:
        with _LOCK, _connect(self._path) as con:
            row = con.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM etf_daily WHERE symbol=?",
                [symbol],
            ).fetchone()
            breakdown = dict(
                con.execute(
                    "SELECT data_source, COUNT(*) FROM etf_daily WHERE symbol=? GROUP BY 1",
                    [symbol],
                ).fetchall()
            )
        return {
            "rows": int(row[0] or 0),
            "first": row[1].isoformat() if row[1] else None,
            "last": row[2].isoformat() if row[2] else None,
            "source_breakdown": {k: int(v) for k, v in breakdown.items()},
        }

    def source_breakdown_for(
        self, symbols: list[str], start: date | None = None, end: date | None = None
    ) -> dict[str, dict[str, int]]:
        """回测区间内各资产的数据源行数分布（响应的 source_status 审计依据）。"""
        with _LOCK, _connect(self._path) as con:
            result: dict[str, dict[str, int]] = {}
            for symbol in symbols:
                query = "SELECT data_source, COUNT(*) FROM etf_daily WHERE symbol=?"
                params: list = [symbol]
                if start is not None:
                    query += " AND trade_date >= ?"
                    params.append(start)
                if end is not None:
                    query += " AND trade_date <= ?"
                    params.append(end)
                rows = con.execute(query + " GROUP BY 1", params).fetchall()
                result[symbol] = {k: int(v) for k, v in rows}
        return result

    # ---- 回测存档 ----
    def save_backtest(self, backtest_id: str, params: dict, result: dict) -> None:
        with _LOCK, _connect(self._path) as con:
            con.execute(
                "INSERT OR REPLACE INTO backtest_runs VALUES (?,?,?,?)",
                [
                    backtest_id,
                    datetime.now(),
                    json.dumps(params, ensure_ascii=False, sort_keys=True),
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                ],
            )

    def load_backtest(self, backtest_id: str) -> dict | None:
        with _LOCK, _connect(self._path) as con:
            row = con.execute(
                "SELECT params_json, result_json FROM backtest_runs WHERE backtest_id=?",
                [backtest_id],
            ).fetchone()
        if not row:
            return None
        return {"params": json.loads(row[0]), "result": json.loads(row[1])}


PROXY_FALLBACK_LABEL = "PROXY"
