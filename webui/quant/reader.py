"""量化面板：直连本地侧车库（只读）的全市场扫描与统计。

原则：
- 只读白名单侧车库（sentiment_v3_replay.duckdb），绝不碰主库
- 全部 SELECT，无任何写副作用
- 形态扫描/宽度/龙虎榜基于 replay 稠密覆盖日（页面上明确标注数据截止）
- 「当日快照」通过 Tushare 全市场单日接口补充（单次调用，TTL 缓存，失败即隐藏）
"""
from __future__ import annotations

import json
import time
from datetime import date as _date, timedelta
from pathlib import Path

import duckdb

from webui.config import QUANT_SNAPSHOT_TTL_SECONDS, REPLAY_DB_PATH

_DENSE_MIN_SYMBOLS = 1000
_LOOKBACK_DAYS = 75

_DENSE_CACHE: dict[str, tuple[float, str | None, int]] = {}


def _database_path() -> Path:
    return Path(REPLAY_DB_PATH)


def connect() -> duckdb.DuckDBPyConnection:
    path = _database_path()
    if not path.is_file():
        raise FileNotFoundError(f"sidecar database not found: {path}")
    return duckdb.connect(str(path), read_only=True)


def dense_latest_date() -> tuple[str | None, int]:
    """最近一个实际全市场稠密覆盖交易日（按当日实际 bar 数判定，带缓存）。"""
    cached = _DENSE_CACHE.get("dense")
    if cached and cached[0] > time.monotonic():
        return cached[1], cached[2]
    with connect() as con:
        row = con.execute(
            """
            SELECT trade_date, COUNT(DISTINCT symbol) AS symbol_count
            FROM replay_stock_base
            GROUP BY trade_date
            HAVING COUNT(DISTINCT symbol) >= ?
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            [_DENSE_MIN_SYMBOLS],
        ).fetchone()
    if row is None:
        _DENSE_CACHE["dense"] = (time.monotonic() + 1800, None, 0)
        return None, 0
    value = (row[0].isoformat(), int(row[1]))
    _DENSE_CACHE["dense"] = (time.monotonic() + 1800, value[0], value[1])
    return value


# ---------------------------------------------------------------------------
# 基础日期
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 市场情绪 / 宽度
# ---------------------------------------------------------------------------

def market_overview(iso_date: str) -> dict | None:
    with connect() as con:
        snap = con.execute(
            """
            SELECT valid_quote_count, advancers, decliners, unchanged,
                   market_return, market_turnover, advance_ratio, decline_ratio,
                   advance_decline_ratio, breadth_strength, market_turnover_change
            FROM replay_market_snapshots
            WHERE trade_date = ?
            ORDER BY trade_date DESC LIMIT 1
            """,
            [iso_date],
        ).fetchone()
        limits = con.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE is_limit_up) AS limit_ups,
              COUNT(*) FILTER (WHERE is_limit_down) AS limit_downs,
              COUNT(*) FILTER (WHERE is_limit_up AND is_broken_board) AS broken_boards,
              SUM(amount) AS total_amount,
              MEDIAN(return_pct) AS median_return
            FROM replay_stock_base
            WHERE trade_date = ? AND NOT is_suspended
            """,
            [iso_date],
        ).fetchone()
    if snap is None or limits is None:
        return None
    (
        valid_quote_count, advancers, decliners, unchanged, market_return,
        market_turnover, advance_ratio, decline_ratio, advance_decline_ratio,
        breadth_strength, market_turnover_change,
    ) = snap
    limit_ups, limit_downs, broken_boards, total_amount, median_return = limits
    return {
        "trade_date": iso_date,
        "valid_quote_count": int(valid_quote_count or 0),
        "advancers": int(advancers or 0),
        "decliners": int(decliners or 0),
        "unchanged": int(unchanged or 0),
        "market_return": float(market_return) * 100 if market_return is not None else None,
        "market_turnover": float(market_turnover) if market_turnover is not None else None,
        "advance_ratio": float(advance_ratio) if advance_ratio is not None else None,
        "breadth_strength": float(breadth_strength) if breadth_strength is not None else None,
        "market_turnover_change": (
            float(market_turnover_change) * 100 if market_turnover_change is not None else None
        ),
        "limit_ups": int(limit_ups or 0),
        "limit_downs": int(limit_downs or 0),
        "broken_boards": int(broken_boards or 0),
        "total_amount": float(total_amount) if total_amount is not None else None,
        "median_return": float(median_return) * 100 if median_return is not None else None,
    }


def breadth_history(days: int = 60) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT s.trade_date, s.advancers, s.decliners, s.unchanged,
                   s.market_return, s.market_turnover, s.advance_ratio,
                   s.breadth_strength,
                   b.limit_ups, b.limit_downs
            FROM replay_market_snapshots s
            LEFT JOIN (
                SELECT trade_date,
                       COUNT(*) FILTER (WHERE is_limit_up) AS limit_ups,
                       COUNT(*) FILTER (WHERE is_limit_down) AS limit_downs
                FROM replay_stock_base
                WHERE NOT is_suspended
                GROUP BY trade_date
            ) b USING (trade_date)
            ORDER BY s.trade_date DESC
            LIMIT ?
            """,
            [days],
        ).fetchall()
    return [
        {
            "trade_date": row[0].isoformat(),
            "advancers": int(row[1] or 0),
            "decliners": int(row[2] or 0),
            "unchanged": int(row[3] or 0),
            "market_return": float(row[4]) * 100 if row[4] is not None else None,
            "market_turnover": float(row[5]) if row[5] is not None else None,
            "advance_ratio": float(row[6]) if row[6] is not None else None,
            "breadth_strength": float(row[7]) if row[7] is not None else None,
            "limit_ups": int(row[8] or 0) if row[8] is not None else None,
            "limit_downs": int(row[9] or 0) if row[9] is not None else None,
        }
        for row in reversed(rows)
    ]


def margin_history(days: int = 60) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT trade_date, market_margin_total
            FROM replay_margin_market
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            [days],
        ).fetchall()
    result = []
    previous = None
    for row in reversed(rows):
        value = float(row[1]) if row[1] is not None else None
        change = None
        if value is not None and previous not in (None, 0):
            change = (value / previous - 1) * 100
        result.append(
            {"trade_date": row[0].isoformat(), "margin_total": value, "change_pct": change}
        )
        previous = value
    return result


# ---------------------------------------------------------------------------
# 涨跌停 / 连板 / 龙虎榜
# ---------------------------------------------------------------------------

def limit_pools(iso_date: str, limit: int = 30) -> dict:
    with connect() as con:
        up_rows = con.execute(
            """
            SELECT b.symbol, COALESCE(m.name, '') AS name, b.close, b.return_pct * 100 AS return_pct,
                   b.volume_ratio, b.turnover_rate * 100 AS turnover_rate
            FROM replay_stock_base b
            LEFT JOIN replay_security_master m USING (symbol)
            WHERE b.trade_date = ? AND b.is_limit_up AND NOT b.is_suspended
            ORDER BY b.return_pct DESC
            LIMIT ?
            """,
            [iso_date, limit],
        ).fetchall()
        down_rows = con.execute(
            """
            SELECT b.symbol, COALESCE(m.name, '') AS name, b.close, b.return_pct * 100 AS return_pct,
                   b.volume_ratio, b.turnover_rate * 100 AS turnover_rate
            FROM replay_stock_base b
            LEFT JOIN replay_security_master m USING (symbol)
            WHERE b.trade_date = ? AND b.is_limit_down AND NOT b.is_suspended
            ORDER BY b.return_pct ASC
            LIMIT ?
            """,
            [iso_date, limit],
        ).fetchall()
    def _rows(rows):
        return [
            {
                "symbol": r[0], "name": r[1], "close": float(r[2]) if r[2] is not None else None,
                "return_pct": float(r[3]) if r[3] is not None else None,
                "volume_ratio": float(r[4]) if r[4] is not None else None,
                "turnover_rate": float(r[5]) if r[5] is not None else None,
            }
            for r in rows
        ]
    return {"trade_date": iso_date, "limit_up_pool": _rows(up_rows), "limit_down_pool": _rows(down_rows)}


def consecutive_boards(iso_date: str, min_streak: int = 2, limit: int = 30) -> list[dict]:
    """连板天梯：截至 iso_date 连续涨停天数 ≥ min_streak 的标的。"""
    with connect() as con:
        rows = con.execute(
            """
            WITH bounds AS (
                SELECT trade_index - 30 AS lo, trade_index AS hi
                FROM replay_calendar_index WHERE trade_date = $as_of
            ),
            daily AS (
                SELECT b.symbol, b.trade_index, b.is_limit_up
                FROM replay_stock_base b, bounds
                WHERE b.trade_index BETWEEN bounds.lo AND bounds.hi
                  AND NOT b.is_suspended
            ),
            islands AS (
                SELECT symbol, trade_index,
                       trade_index - ROW_NUMBER() OVER (
                           PARTITION BY symbol ORDER BY trade_index
                       ) AS grp
                FROM daily WHERE is_limit_up
            ),
            streaks AS (
                SELECT symbol, MAX(trade_index) AS end_idx, COUNT(*) AS length
                FROM islands GROUP BY symbol, grp
            )
            SELECT st.symbol, COALESCE(m.name, '') AS name, st.length,
                   bb.close, bb.return_pct * 100 AS return_pct, bb.turnover_rate * 100 AS turnover_rate
            FROM streaks st
            JOIN replay_stock_base bb
              ON bb.symbol = st.symbol AND bb.trade_index = st.end_idx
            LEFT JOIN replay_security_master m ON m.symbol = st.symbol
            WHERE st.end_idx = (SELECT hi FROM bounds)
              AND st.length >= $min_streak
            ORDER BY st.length DESC, bb.return_pct DESC
            LIMIT $limit
            """,
            {"as_of": iso_date, "min_streak": min_streak, "limit": limit},
        ).fetchall()
    return [
        {
            "symbol": r[0], "name": r[1], "streak": int(r[2]),
            "close": float(r[3]) if r[3] is not None else None,
            "return_pct": float(r[4]) if r[4] is not None else None,
            "turnover_rate": float(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ]


_DRAGON_REASON_LABELS = {
    "日涨幅偏离值达7%的证券": "日涨幅偏离7%",
    "日换手率达20%的证券": "日换手20%",
    "日振幅值达15%的证券": "日振幅15%",
    "连续三个交易日内，涨幅偏离值累计达20%的证券": "三日涨幅偏离20%",
    "有价格涨跌幅限制的日跌幅达7%的证券": "日跌幅7%",
    "ST、*ST和S证券连续三个交易日内，跌幅偏离值累计达15%的证券": "ST三日跌幅偏离15%",
}


def dragon_tiger(iso_date: str, limit: int = 30) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT e.symbol, COALESCE(m.name, '') AS name, e.buy_amount, e.sell_amount,
                   e.net_amount, e.institution_net_amount, e.seat_count,
                   e.institution_seat_count, e.reasons_json
            FROM replay_dragon_tiger_events e
            LEFT JOIN replay_security_master m USING (symbol)
            WHERE e.trade_date = ?
            ORDER BY ABS(e.net_amount) DESC
            LIMIT ?
            """,
            [iso_date, limit],
        ).fetchall()
    items = []
    for row in rows:
        try:
            reasons_raw = json.loads(row[8]) if isinstance(row[8], str) else (row[8] or [])
        except (TypeError, ValueError):
            reasons_raw = []
        reasons = [_DRAGON_REASON_LABELS.get(str(r), str(r)) for r in reasons_raw]
        items.append(
            {
                "symbol": row[0],
                "name": row[1],
                "buy_amount": float(row[2]) if row[2] is not None else None,
                "sell_amount": float(row[3]) if row[3] is not None else None,
                "net_amount": float(row[4]) if row[4] is not None else None,
                "institution_net_amount": float(row[5]) if row[5] is not None else None,
                "seat_count": int(row[6]) if row[6] is not None else None,
                "institution_seat_count": int(row[7]) if row[7] is not None else None,
                "reasons": reasons,
            }
        )
    return items


# ---------------------------------------------------------------------------
# 形态扫描（全部基于复权价，单一公式 adj_close = close * adj_factor）
# ---------------------------------------------------------------------------

_SCAN_WINDOW_SQL = f"""
    WITH w AS (
        SELECT b.symbol, b.trade_date, b.trade_index, b.close, b.volume, b.amount,
               b.return_pct, b.volume_ratio, b.turnover_rate,
               b.close * b.adj_factor AS ac,
               AVG(b.close * b.adj_factor) OVER (
                   PARTITION BY b.symbol ORDER BY b.trade_index
                   ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ma5,
               AVG(b.close * b.adj_factor) OVER (
                   PARTITION BY b.symbol ORDER BY b.trade_index
                   ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) AS ma10,
               AVG(b.close * b.adj_factor) OVER (
                   PARTITION BY b.symbol ORDER BY b.trade_index
                   ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
               AVG(b.close * b.adj_factor) OVER (
                   PARTITION BY b.symbol ORDER BY b.trade_index
                   ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60,
               MAX(b.close * b.adj_factor) OVER (
                   PARTITION BY b.symbol ORDER BY b.trade_index
                   ROWS BETWEEN 19 PRECEDING AND 1 PRECEDING) AS prev_high20
        FROM replay_stock_base b
        WHERE b.trade_index >= (SELECT trade_index - {_LOOKBACK_DAYS}
                                FROM replay_calendar_index WHERE trade_date = $as_of)
          AND b.trade_index <= (SELECT trade_index FROM replay_calendar_index WHERE trade_date = $as_of)
          AND NOT b.is_suspended
    ),
    lagged AS (
        SELECT *,
               LAG(ma5) OVER (PARTITION BY symbol ORDER BY trade_index) AS ma5_prev,
               LAG(ma20) OVER (PARTITION BY symbol ORDER BY trade_index) AS ma20_prev,
               LAG(ac) OVER (PARTITION BY symbol ORDER BY trade_index) AS ac_prev
        FROM w
    )
    SELECT l.symbol, COALESCE(m.name, '') AS name, l.close, l.return_pct * 100 AS return_pct,
           l.volume_ratio, l.turnover_rate * 100 AS turnover_rate, l.amount,
           l.ac / NULLIF(l.ma20, 0) - 1 AS ma20_dev,
           l.ac / NULLIF(l.ma60, 0) - 1 AS ma60_dev
    FROM lagged l
    LEFT JOIN replay_security_master m USING (symbol)
    WHERE l.trade_date = $as_of
"""

_CONDITIONS = {
    "ma_bull": "l.ac > l.ma5 AND l.ma5 > l.ma10 AND l.ma10 > l.ma20 AND l.ma20 > l.ma60 AND l.ma5 >= l.ma5_prev",
    "golden_cross": "l.ac > l.ma5 AND l.ma5 > l.ma20 AND l.ma5_prev <= l.ma20_prev",
    "dead_cross": "l.ac < l.ma5 AND l.ma5 < l.ma20 AND l.ma5_prev >= l.ma20_prev",
    "breakout_20d": "l.ac >= l.prev_high20 AND l.ac_prev < l.prev_high20",
    "volume_surge": "l.volume_ratio >= 2.0 AND l.return_pct > 0.01",
    "ma_bear": "l.ac < l.ma5 AND l.ma5 < l.ma10 AND l.ma10 < l.ma20 AND l.ma20 < l.ma60",
}

SCAN_TYPES = ("ma_bull", "ma_bear", "golden_cross", "dead_cross", "breakout_20d", "volume_surge")


def scan(scan_type: str, iso_date: str, limit: int = 50) -> dict:
    if scan_type not in _CONDITIONS:
        raise ValueError(f"unknown scan type: {scan_type}")
    base_sql = _SCAN_WINDOW_SQL + f" AND ({_CONDITIONS[scan_type]})"
    if scan_type == "breakout_20d":
        base_sql += " AND l.prev_high20 IS NOT NULL"
    order = "l.return_pct" if scan_type != "volume_surge" else "l.volume_ratio"
    items_sql = base_sql + f"\nORDER BY {order} DESC\nLIMIT {int(limit)}"
    total_sql = f"SELECT COUNT(*) FROM ({base_sql}) t"
    with connect() as con:
        rows = con.execute(items_sql, {"as_of": iso_date}).fetchall()
        total = con.execute(total_sql, {"as_of": iso_date}).fetchone()[0]
    items = []
    for row in rows:
        items.append(
            {
                "symbol": row[0],
                "name": row[1],
                "close": float(row[2]) if row[2] is not None else None,
                "return_pct": float(row[3]) if row[3] is not None else None,
                "volume_ratio": float(row[4]) if row[4] is not None else None,
                "turnover_rate": float(row[5]) if row[5] is not None else None,
                "amount": float(row[6]) if row[6] is not None else None,
                "ma20_dev_pct": float(row[7]) * 100 if row[7] is not None else None,
                "ma60_dev_pct": float(row[8]) * 100 if row[8] is not None else None,
            }
        )
    return {"scan_type": scan_type, "trade_date": iso_date, "total": int(total), "items": items}


def rps_leaders(iso_date: str, days: int = 60, min_rps: float = 90.0, limit: int = 50) -> dict:
    """相对强度 RPS：N 日复权收益在全市场的百分位（欧奈尔口径，辅助）。"""
    with connect() as con:
        rows = con.execute(
            """
            WITH w AS (
                SELECT symbol, trade_date, trade_index, close * adj_factor AS ac
                FROM replay_stock_base
                WHERE trade_index >= (SELECT trade_index - $lookback
                                      FROM replay_calendar_index WHERE trade_date = $as_of)
                  AND trade_index <= (SELECT trade_index FROM replay_calendar_index WHERE trade_date = $as_of)
                  AND NOT is_suspended
            ),
            rets AS (
                SELECT symbol, trade_date, trade_index, ac,
                       LAG(ac, $days) OVER (PARTITION BY symbol ORDER BY trade_index) AS ac_base
                FROM w
            ),
            r60 AS (
                SELECT symbol, trade_date, (ac / NULLIF(ac_base, 0) - 1) * 100 AS ret60
                FROM rets WHERE trade_date = $as_of AND ac_base > 0
            ),
            ranked AS (
                SELECT symbol, trade_date, ret60,
                       PERCENT_RANK() OVER (ORDER BY ret60) * 100 AS rps
                FROM r60 WHERE ret60 IS NOT NULL
            )
            SELECT r.symbol, COALESCE(m.name, '') AS name, r.ret60, r.rps,
                   b.close, b.return_pct * 100 AS return_pct
            FROM ranked r
            LEFT JOIN replay_security_master m ON m.symbol = r.symbol
            JOIN replay_stock_base b
              ON b.symbol = r.symbol AND b.trade_date = $as_of
            WHERE r.rps >= $min_rps
            ORDER BY r.rps DESC
            LIMIT $limit
            """,
            {"as_of": iso_date, "lookback": _LOOKBACK_DAYS, "days": days,
             "min_rps": min_rps, "limit": limit},
        ).fetchall()
    items = [
        {
            "symbol": row[0],
            "name": row[1],
            "ret60_pct": float(row[2]) if row[2] is not None else None,
            "rps": round(float(row[3]), 1) if row[3] is not None else None,
            "close": float(row[4]) if row[4] is not None else None,
            "return_pct": float(row[5]) if row[5] is not None else None,
        }
        for row in rows
    ]
    return {"trade_date": iso_date, "days": days, "min_rps": min_rps, "items": items}


# ---------------------------------------------------------------------------
# 当日快照（Tushare 全市场单日补充，TTL 缓存，失败返回 None）
# ---------------------------------------------------------------------------

_SNAPSHOT_CACHE: dict[str, tuple[float, dict | None]] = {}


def _latest_session_sync(today: _date) -> dict | None:
    from config.settings import settings
    from data_hub.providers.tushare_provider import TushareProvider

    provider = TushareProvider(token=settings.tushare_token)
    frame = None
    used_date: _date | None = None
    for offset in range(0, 12):
        candidate = today - timedelta(days=offset)
        attempt = provider._client.daily(trade_date=candidate.strftime("%Y%m%d"))
        if attempt is not None and not attempt.empty:
            frame = attempt
            used_date = candidate
            break
    if frame is None or used_date is None:
        return None
    import pandas as pd

    frame = frame.copy()
    frame["pct_chg"] = pd.to_numeric(frame["pct_chg"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame["code6"] = frame["ts_code"].str.slice(0, 6)
    advancers = int((frame["pct_chg"] > 0).sum())
    decliners = int((frame["pct_chg"] < 0).sum())
    flats = int((frame["pct_chg"] == 0).sum())

    def _limit_threshold(code: str) -> float:
        if code.startswith(("30", "68")):
            return 19.85
        if code.startswith(("4", "8", "92")):
            return 29.85
        return 9.85

    thresholds = frame["code6"].map(_limit_threshold)
    limit_ups = int((frame["pct_chg"] >= thresholds).sum())
    limit_downs = int((frame["pct_chg"] <= -thresholds).sum())

    def _top(sort_col: str, ascending: bool, n: int = 20) -> list[dict]:
        head = frame.dropna(subset=[sort_col]).sort_values(sort_col, ascending=ascending).head(n)
        return [
            {
                "symbol": _to_canonical(str(row["ts_code"])),
                "name": _name_of(str(row["ts_code"])),
                "close": float(row["close"]) if row["close"] is not None else None,
                "return_pct": float(row["pct_chg"]) if row["pct_chg"] is not None else None,
                "amount": float(row["amount"]) if row["amount"] is not None else None,
            }
            for _, row in head.iterrows()
        ]

    return {
        "trade_date": used_date.isoformat(),
        "advancers": advancers,
        "decliners": decliners,
        "flats": flats,
        "limit_ups_approx": limit_ups,
        "limit_downs_approx": limit_downs,
        "total_amount": float(frame["amount"].sum(skipna=True)) * 1000,  # 千元 → 元
        "gainers": _top("pct_chg", False),
        "losers": _top("pct_chg", True),
        "top_amount": _top("amount", False),
        "note": "当日口径为近似值：涨停按主板10%/创业科创20%/北交所30%阈值判定，ST 未单独识别",
    }


_NAME_CACHE: dict[str, str] = {}


def _load_names() -> None:
    if _NAME_CACHE:
        return
    try:
        with connect() as con:
            rows = con.execute(
                "SELECT symbol, name FROM replay_security_master"
            ).fetchall()
        for symbol, name in rows:
            _NAME_CACHE[str(symbol).split(".")[0]] = str(name)
            _NAME_CACHE[str(symbol)] = str(name)
    except Exception:
        return


def _name_of(ts_code: str) -> str | None:
    _load_names()
    code6 = ts_code.split(".")[0]
    return _NAME_CACHE.get(ts_code) or _NAME_CACHE.get(code6) or code6


def _to_canonical(ts_code: str) -> str:
    code, _, exchange = ts_code.partition(".")
    return f"{code}.{exchange}" if exchange else code


def latest_session() -> dict | None:
    """当日全市场快照（Tushare 单次调用；TTL 缓存；任何失败 → None）。"""
    cached = _SNAPSHOT_CACHE.get("latest")
    if cached and cached[0] > time.monotonic():
        return cached[1]
    value = None
    try:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            value = pool.submit(_latest_session_sync, _date.today()).result(timeout=120)
    except Exception:
        value = None
    _SNAPSHOT_CACHE["latest"] = (time.monotonic() + QUANT_SNAPSHOT_TTL_SECONDS, value)
    return value


__all__ = [
    "connect",
    "dense_latest_date",
    "market_overview",
    "breadth_history",
    "margin_history",
    "limit_pools",
    "consecutive_boards",
    "dragon_tiger",
    "scan",
    "SCAN_TYPES",
    "rps_leaders",
    "latest_session",
]
