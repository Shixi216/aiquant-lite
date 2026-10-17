"""GET-only 量化面板端点：直连侧车库的全市场统计与扫描。

- 形态扫描/宽度/龙虎榜：replay 侧车库稠密覆盖日（响应带 data_cutoff）
- /latest：Tushare 当日全市场快照（可选补充，失败时 available=false）
"""
from __future__ import annotations

from fastapi import APIRouter, Query

import webui.quant.reader as reader

router = APIRouter(prefix="/webui/v2/quant", tags=["webui-quant"])


def _ok(data, **meta) -> dict:
    return {"available": True, "reason_code": None, **meta, "data": data}


def _fail(reason_code: str) -> dict:
    return {"available": False, "reason_code": reason_code, "data": None}


def _sentiment_temperature(overview: dict) -> float:
    """辅助情绪温度：上涨占比与涨跌停差加权的 0-100 读数（非正式口径）。"""
    advance_ratio = overview.get("advance_ratio") or 0.5
    limit_ups = overview.get("limit_ups") or 0
    limit_downs = overview.get("limit_downs") or 0
    denominator = max(limit_ups + limit_downs, 10)
    raw = 50.0 + (advance_ratio - 0.5) * 80.0 + (limit_ups - limit_downs) / denominator * 15.0
    return round(max(0.0, min(100.0, raw)), 1)


@router.get("/overview")
def quant_overview() -> dict:
    try:
        as_of, symbol_count = reader.dense_latest_date()
        if as_of is None:
            return _fail("no_local_history")
        overview = reader.market_overview(as_of)
        if overview is None:
            return _fail("no_local_history")
        boards = reader.consecutive_boards(as_of, min_streak=2, limit=20)
        return _ok(
            {
                "sentiment": overview,
                "temperature": _sentiment_temperature(overview),
                "consecutive_boards": boards,
                "universe_size": symbol_count,
            },
            data_cutoff=as_of,
            data_source="local_replay_history",
            formal_status="auxiliary",
        )
    except FileNotFoundError:
        return _fail("sidecar_missing")
    except Exception:
        return _fail("quant_read_error")


@router.get("/breadth")
def quant_breadth(days: int = Query(default=60, ge=10, le=250)) -> dict:
    try:
        return _ok(
            reader.breadth_history(days),
            data_source="local_replay_history",
            formal_status="auxiliary",
        )
    except Exception:
        return _fail("quant_read_error")


@router.get("/margin")
def quant_margin(days: int = Query(default=60, ge=10, le=250)) -> dict:
    try:
        return _ok(
            reader.margin_history(days),
            data_source="local_replay_history",
            formal_status="auxiliary",
        )
    except Exception:
        return _fail("quant_read_error")


@router.get("/dragon-tiger")
def quant_dragon_tiger(limit: int = Query(default=30, ge=5, le=100)) -> dict:
    try:
        as_of, _count = reader.dense_latest_date()
        if as_of is None:
            return _fail("no_local_history")
        return _ok(
            reader.dragon_tiger(as_of, limit),
            data_cutoff=as_of,
            data_source="local_replay_history",
            formal_status="auxiliary",
        )
    except Exception:
        return _fail("quant_read_error")


@router.get("/limit-pools")
def quant_limit_pools(limit: int = Query(default=30, ge=5, le=100)) -> dict:
    try:
        as_of, _count = reader.dense_latest_date()
        if as_of is None:
            return _fail("no_local_history")
        return _ok(
            reader.limit_pools(as_of, limit),
            data_cutoff=as_of,
            data_source="local_replay_history",
            formal_status="auxiliary",
        )
    except Exception:
        return _fail("quant_read_error")


@router.get("/scan")
def quant_scan(
    type: str = Query(default="ma_bull"),
    limit: int = Query(default=50, ge=5, le=200),
) -> dict:
    if type not in reader.SCAN_TYPES:
        return {"available": False, "reason_code": "unknown_scan_type", "data": None}
    try:
        as_of, _count = reader.dense_latest_date()
        if as_of is None:
            return _fail("no_local_history")
        result = reader.scan(type, as_of, limit)
        return _ok(
            result,
            data_cutoff=as_of,
            data_source="local_replay_history",
            formal_status="auxiliary",
        )
    except Exception:
        return _fail("quant_read_error")


@router.get("/rps")
def quant_rps(
    days: int = Query(default=60, ge=20, le=250),
    min_rps: float = Query(default=90.0, ge=50.0, le=100.0),
    limit: int = Query(default=50, ge=5, le=200),
) -> dict:
    try:
        as_of, _count = reader.dense_latest_date()
        if as_of is None:
            return _fail("no_local_history")
        return _ok(
            reader.rps_leaders(as_of, days=days, min_rps=min_rps, limit=limit),
            data_cutoff=as_of,
            data_source="local_replay_history",
            formal_status="auxiliary",
        )
    except Exception:
        return _fail("quant_read_error")


@router.get("/latest")
def quant_latest_session() -> dict:
    """当日全市场快照（Tushare 补充；不可用时 available=false，前端隐藏该区）。"""
    value = reader.latest_session()
    if value is None:
        return _fail("latest_session_unavailable")
    return _ok(
        value,
        data_cutoff=value["trade_date"],
        data_source="provider_latest_session",
        formal_status="auxiliary",
    )


__all__ = ["router"]
