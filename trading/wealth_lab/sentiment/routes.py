"""Wealth Sentiment 独立 API（/v1/wealth-lab/sentiment/*）。

不修改任何既有冻结契约与股票业务接口；只新增独立路由。
POST /refresh 走 v3.2 任务控制中心真实执行（返回 job_id，前端可轮询）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from trading.wealth_lab.schemas import envelope
from trading.wealth_lab.sentiment import repository as repo, service

_TZ = timezone(timedelta(hours=8))
API_VERSION = "wealth-sentiment/v1"

router = APIRouter(prefix="/v1/wealth-lab/sentiment", tags=["wealth-sentiment"])


def _env(data, **kwargs):
    return envelope(data, api_version=API_VERSION, **kwargs)


def _parse_json_fields(row: dict, *fields: str) -> dict:
    for field_name in fields:
        value = row.get(field_name)
        if isinstance(value, str):
            try:
                row[field_name] = json.loads(value)
            except Exception:
                pass
    return row


class RefreshBody(BaseModel):
    """提交资讯更新任务（走任务控制中心，真实执行）。"""
    model_config = ConfigDict(extra="forbid")

    sources: list[str] | None = None
    fund_symbols: list[str] | None = None
    confirm: bool = False


class AnalyzeBody(BaseModel):
    """提交 LLM 情绪分析任务（真实付费模型调用，必须 confirm=true）。"""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=8, ge=1, le=20)
    confirm: bool = False


class RebuildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_hours: int = Field(default=168, ge=1, le=2160)


# ------------------------------------------------------------------ 查询接口
@router.get("/news/events")
def news_events(
    category: str | None = None,
    symbol: str | None = None,
    analysis_status: str | None = None,
    since: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    since_dt = _parse_since(since)
    rows = repo.list_events(category=category, symbol=symbol, since=since_dt,
                            limit=limit, analysis_status=analysis_status)
    for row in rows:
        _parse_json_fields(row, "asset_scope", "asset_links")
    return _env({"data_mode": "REAL", "events": rows, "count": len(rows)})


@router.get("/sources")
def sources():
    return _env({"data_mode": "REAL", "sources": service.source_registry(),
                 "rejected": _rejected_sources()})


@router.get("/assets/{symbol}/news")
def asset_news(symbol: str, limit: int = Query(default=30, ge=1, le=100)):
    _require_pool_symbol(symbol)
    rows = repo.asset_news(symbol, limit)
    return _env({"data_mode": "REAL", "symbol": symbol, "items": rows, "count": len(rows)})


@router.get("/market")
def market_sentiment():
    rows = repo.latest_scores("MARKET", None, limit=1)
    if not rows:
        return _env({"data_mode": "REAL", "score": None,
                     "evidence_basis": "INSUFFICIENT_DATA",
                     "message": "尚无市场情绪快照：请先执行 rebuild_wealth_sentiment_factor 任务"})
    return _env({"data_mode": "REAL", "score": _score_public(rows[0])})


@router.get("/market/history")
def market_history(days: int = Query(default=30, ge=1, le=365)):
    since = datetime.now(_TZ) - timedelta(days=days)
    rows = repo.score_history("MARKET", None, since)
    return _env({"data_mode": "REAL",
                 "history": [_score_public(r, with_breakdown=False) for r in rows]})


@router.get("/assets/{symbol}/score")
def asset_score(symbol: str):
    _require_pool_symbol(symbol)
    rows = repo.latest_scores("ASSET", symbol, limit=1)
    if not rows:
        return _env({"data_mode": "REAL", "symbol": symbol, "score": None,
                     "evidence_basis": "INSUFFICIENT_DATA",
                     "message": "该资产窗口内无有效观测（evidence_basis=INSUFFICIENT_DATA）"})
    return _env({"data_mode": "REAL", "symbol": symbol, "score": _score_public(rows[0])})


@router.get("/assets/{symbol}/history")
def asset_history(symbol: str, days: int = Query(default=30, ge=1, le=365)):
    _require_pool_symbol(symbol)
    since = datetime.now(_TZ) - timedelta(days=days)
    rows = repo.score_history("ASSET", symbol, since)
    return _env({"data_mode": "REAL", "symbol": symbol,
                 "history": [_score_public(r, with_breakdown=False) for r in rows]})


@router.get("/scores/{score_id}/evidence")
def score_evidence(score_id: str):
    detail = repo.evidence_for_score(score_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="score not found")
    for analysis in detail.get("analyses", []):
        _parse_json_fields(analysis, "affected_asset_classes", "affected_symbols",
                           "evidence_spans", "validation_flags")
    return _env({"data_mode": "REAL", "score": detail})


# ------------------------------------------------------------------ 任务提交
@router.post("/refresh")
def submit_refresh(body: RefreshBody | None = None):
    body = body or RefreshBody()
    from trading.wealth_lab.jobs import manager
    params: dict = {"sources": body.sources, "fund_symbols": body.fund_symbols}
    params = {k: v for k, v in params.items() if v}
    job, duplicated = manager.submit("refresh_wealth_news", params)
    return _env({"data_mode": "REAL", **job.public(), "duplicated": duplicated,
                 "message": "采集任务已提交（真实执行，轮询 jobs 接口获取进度）"})


@router.post("/analyze")
def submit_analyze(body: AnalyzeBody):
    if not body.confirm:
        raise HTTPException(status_code=422,
                            detail="analyze_wealth_sentiment 涉及真实付费模型调用，需 confirm=true")
    from trading.wealth_lab.jobs import manager
    job, duplicated = manager.submit("analyze_wealth_sentiment",
                                     {"limit": body.limit, "confirm": True})
    return _env({"data_mode": "REAL", **job.public(), "duplicated": duplicated,
                 "message": "分析任务已提交（deepseek-flash 优先，qwen 兜底）"})


@router.post("/rebuild-factor")
def submit_rebuild(body: RebuildBody | None = None):
    body = body or RebuildBody()
    from trading.wealth_lab.jobs import manager
    job, duplicated = manager.submit("rebuild_wealth_sentiment_factor",
                                     {"lookback_hours": body.lookback_hours})
    return _env({"data_mode": "REAL", **job.public(), "duplicated": duplicated,
                 "message": "因子重建任务已提交（确定性聚合）"})


# ------------------------------------------------------------------ helpers
def _require_pool_symbol(symbol: str) -> None:
    from trading.wealth_lab.sentiment.config import SYMBOL_CLASS
    if symbol not in SYMBOL_CLASS:
        raise HTTPException(status_code=404, detail=f"symbol 不在 Wealth Lab 研究池: {symbol}")


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since)
        return dt if dt.tzinfo else dt.replace(tzinfo=_TZ)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="since 需要 ISO 时间格式") from exc


def _score_public(row: dict, with_breakdown: bool = True) -> dict:
    out = {
        "score_id": row["score_id"], "level": row["level"], "symbol": row["symbol"],
        "asof": row["asof"].isoformat() if getattr(row["asof"], "isoformat", None) else row["asof"],
        "sentiment_score": row["sentiment_score"], "confidence": row["confidence"],
        "coverage": row.get("coverage"),
        "coverage_ratio": row.get("coverage"),
        "observation_count": row["observation_count"],
        "source_count": row["source_count"], "sentiment_version": row["sentiment_version"],
        "evidence_basis": row.get("evidence_basis"),
        "evidence_count": row.get("evidence_count"),
        "direct_evidence_count": row.get("direct_evidence_count"),
        "class_evidence_count": row.get("class_evidence_count"),
        "last_evidence_at": (row["last_evidence_at"].isoformat()
                             if getattr(row.get("last_evidence_at"), "isoformat", None)
                             else row.get("last_evidence_at")),
    }
    evidence = row.get("evidence_ids")
    out["evidence_ids"] = json.loads(evidence) if isinstance(evidence, str) else (evidence or [])
    if with_breakdown:
        breakdown = row.get("component_breakdown")
        out["component_breakdown"] = json.loads(breakdown) if isinstance(breakdown, str) else breakdown
    return out


def _rejected_sources() -> dict:
    from trading.wealth_lab.sentiment.config import REJECTED_SOURCES
    return REJECTED_SOURCES
