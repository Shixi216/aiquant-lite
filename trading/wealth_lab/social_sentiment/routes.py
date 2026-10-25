"""Wealth Social Sentiment 独立 API（/v1/wealth-lab/social-sentiment/*）。

合规不式：所有写操作经 Source Authorization Registry 闸门；
未授权平台的采集接口返回 403 并展示具体原因（前端据此禁用按钮）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from trading.wealth_lab.schemas import envelope
from trading.wealth_lab.social_sentiment import ingest, registry, wordcloud
from trading.wealth_lab.social_sentiment import repository as repo
from trading.wealth_lab.social_sentiment.analyzer import aggregate_research

API_VERSION = "social-sentiment/v1"
router = APIRouter(prefix="/v1/wealth-lab/social-sentiment", tags=["social-sentiment"])


def _env(data, **kwargs):
    return envelope(data, api_version=API_VERSION, **kwargs)


class CommentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")  # PII 字段直接 422

    content: str = Field(min_length=4, max_length=2000)
    origin_url: str | None = None
    topic: str | None = None
    symbol: str | None = None
    parent_comment_id: str | None = None
    published_at: str | None = None


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    comments: list[CommentItem] = Field(min_length=1, max_length=500)
    authorization_assertion: str | None = Field(
        default=None, max_length=1000,
        description="授权依据声明（manual-authorized-file 来源必填）")
    data_origin: str = Field(default="real", pattern="^(real|synthetic_test)$")


class CollectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str


class AnalyzeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=60)
    confirm: bool = False
    include_synthetic: bool = False


class SummaryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = None
    platform: str | None = None
    confirm: bool = False


@router.get("/sources")
def sources():
    """注册表全量：状态/授权范围/申请渠道/禁用原因 + 数据覆盖。"""
    return _env({"data_mode": "REAL", "sources": registry.list_sources(),
                 "coverage": repo.coverage_stats(include_synthetic=False),
                 "principle": "未授权采集不得上线；robots.txt 不构成授权"})


@router.post("/import")
def import_comments(body: ImportBody):
    """导入授权数据/获准文件（闸门校验 + PII 拒绝 + 去重）。"""
    try:
        result = ingest.import_comments(
            body.source_id, [c.model_dump() for c in body.comments],
            authorization_assertion=body.authorization_assertion,
            data_origin=body.data_origin)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _env({"data_mode": "REAL", **result})


@router.post("/collect")
def collect(body: CollectBody):
    """自动采集（受闸门控制）：当前没有任何源获得 auto_collect 授权 → 一律 403。"""
    result = ingest.collect_from_platform(body.source_id)
    if not result.get("ok"):
        raise HTTPException(status_code=403, detail=result["reason"])
    return _env({"data_mode": "REAL", **result})


@router.post("/analyze")
def analyze(body: AnalyzeBody):
    """deepseek-flash 批量结构化分类（真实付费调用，需 confirm）。"""
    if not body.confirm:
        raise HTTPException(status_code=422,
                            detail="analyze 涉及真实付费模型调用，需 confirm=true")
    from trading.wealth_lab.social_sentiment.analyzer import classify_batch
    result = classify_batch(limit=body.limit, include_synthetic=body.include_synthetic)
    if result.get("status") == "ANALYSIS_FAILED":
        raise HTTPException(status_code=502, detail=result["error"])
    return _env({"data_mode": "REAL", **result})


@router.post("/summary")
def summary(body: SummaryBody):
    """qwen 按需生成投资者观点摘要（真实付费调用，需 confirm）。"""
    if not body.confirm:
        raise HTTPException(status_code=422,
                            detail="summary 涉及真实付费模型调用，需 confirm=true")
    from trading.wealth_lab.social_sentiment.analyzer import summarize_views
    return _env({"data_mode": "REAL", **summarize_views(body.symbol, body.platform)})


@router.get("/research")
def research(symbol: str | None = None, platform: str | None = None,
             lookback_hours: int = Query(default=168, ge=1, le=2160)):
    """情绪研究聚合：观点分布/多空理由/热门话题/热度/分歧/变化/证据覆盖度。"""
    return _env({"data_mode": "REAL",
                 **aggregate_research(symbol=symbol, platform=platform,
                                      lookback_hours=lookback_hours)})


@router.get("/wordcloud")
def cloud(symbol: str | None = None, platform: str | None = None,
          top_k: int = Query(default=50, ge=5, le=200),
          include_synthetic: bool = False):
    """确定性词频（jieba 分词，非 LLM 生成）。"""
    return _env({"data_mode": "REAL",
                 **wordcloud.word_frequency(symbol=symbol, platform=platform,
                                            top_k=top_k,
                                            include_synthetic=include_synthetic)})


@router.get("/sentiment/distribution")
def distribution(symbol: str | None = None):
    return _env({"data_mode": "REAL",
                 **aggregate_research(symbol=symbol).get("distribution", {})})


@router.get("/platform-comparison")
def platform_comparison():
    """平台对比：各平台观点分布与样本量（仅真实数据）。"""
    from trading.wealth_lab.social_sentiment import registry as reg
    platforms = sorted({s["platform"] for s in reg.list_sources()})
    out = {}
    for plat in platforms:
        agg = aggregate_research(platform=plat)
        out[plat] = ({"status": agg["status"], "distribution": agg.get("distribution"),
                      "sample_size": agg.get("sample_size", 0),
                      "divergence": agg.get("opinion_divergence")}
                     if agg.get("status") == "OK" else {"status": agg["status"]})
    return _env({"data_mode": "REAL", "platforms": out})


@router.get("/heatmap")
def heatmap():
    """资产 × 平台 舆情热力图（评论数 + 净情绪），仅真实数据。"""
    comments = repo.analyzed_comments(include_synthetic=False)
    cells = {}
    for c in comments:
        key = (c.get("symbol") or "UNLINKED", c["platform"])
        cell = cells.setdefault(key, {"comments": 0, "positive": 0, "negative": 0})
        cell["comments"] += 1
        if c["stance"] == "POSITIVE":
            cell["positive"] += 1
        elif c["stance"] == "NEGATIVE":
            cell["negative"] += 1
    out = [{"symbol": s, "platform": p, "comments": v["comments"],
            "net_sentiment": v["positive"] - v["negative"]}
           for (s, p), v in sorted(cells.items())]
    return _env({"data_mode": "REAL", "cells": out,
                 "note": "无已分析真实评论时 cells 为空（不编造热力）"})


@router.get("/coverage")
def coverage():
    """样本量与覆盖率 + 来源授权状态。"""
    return _env({"data_mode": "REAL", "coverage": repo.coverage_stats(False),
                 "sources_status": [{k: s[k] for k in ("source_id", "status",
                                                      "collector_enabled",
                                                      "disabled_reason")}
                                    for s in registry.list_sources()]})


@router.post("/registry/status")
def set_registry_status(body: dict):
    """（管理）授权状态变更：必须提交授权证据；AUTHORIZED 需人工书面依据。"""
    from fastapi import Request  # noqa: F401
    try:
        source = registry.set_status(
            body.get("source_id", ""), body.get("status", ""),
            evidence=body.get("evidence", ""),
            permitted_operations=body.get("permitted_operations"),
            retention_limit_days=body.get("retention_limit_days"))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _env({"data_mode": "REAL", "source": {
        k: source[k] for k in ("source_id", "status", "permitted_operations")}})
