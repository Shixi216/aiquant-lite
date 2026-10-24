"""确定性情绪聚合：Market Sentiment + Asset Sentiment 两层。

由确定性程序（非 LLM）根据相关性、可信度、新颖性、时效性与模型置信度聚合；
LLM 只提供单事件结构化判断。权重 WEIGHTS_V1 全量随快照入库可查，
版本号 SENTIMENT_VERSION 变更必须保留旧版本历史快照。
情绪分数是研究指标：-100~+100，不等于预期收益方向，不构成统计预测概率。
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone

from trading.wealth_lab.sentiment import repository as repo
from trading.wealth_lab.sentiment.config import (
    FACTOR_LOOKBACK_HOURS, SENTIMENT_VERSION, SYMBOL_CLASS, WEIGHTS_V1,
)

_TZ = timezone(timedelta(hours=8))


def _decay(published_at: datetime, asof: datetime, raw_category: str | None) -> float:
    half_life = WEIGHTS_V1["half_life_hours"].get(raw_category or "GENERAL", 48.0)
    age_hours = max((asof - published_at).total_seconds() / 3600.0, 0.0)
    return math.exp(-math.log(2.0) * age_hours / half_life)


def _direction_value(direction: str | None) -> float:
    return {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0,
            "MIXED": WEIGHTS_V1["mixed_direction_scale"]}.get(direction or "", 0.0)


def _event_contribution(event: dict, asof: datetime, importance: int | None = None) -> dict:
    """单事件基础贡献（不含资产关联权重）：方向×影响×置信×时效×新颖性。"""
    impact = event.get("estimated_impact") or "MINOR"
    weight = WEIGHTS_V1["impact"].get(impact, 0.4)
    if importance is not None:
        weight = WEIGHTS_V1["eco_importance"].get(importance, weight)
    decay = _decay(event["published_at"], asof, event.get("raw_category"))
    diversity = min(
        WEIGHTS_V1["source_diversity_cap"],
        1.0 + WEIGHTS_V1["source_diversity_step"] * (max(event.get("source_count", 1), 1) - 1),
    )
    confidence = min(max(float(event.get("confidence") or 0.0), 0.0), 1.0)
    value = _direction_value(event.get("sentiment_direction"))
    return {
        "analysis_id": event["analysis_id"], "event_id": event["event_id"],
        "title": event["title"], "published_at": str(event["published_at"]),
        "direction": event.get("sentiment_direction"), "impact": impact,
        "classes": json.loads(event.get("affected_asset_classes") or "[]"),
        "symbols": json.loads(event.get("affected_symbols") or "[]"),
        "event_asset_scope": json.loads(event.get("event_asset_scope") or "[]"),
        "confidence": confidence,
        "w": weight * decay * diversity,
        "signed": value * weight * decay * diversity * confidence,
        "decay": round(decay, 4),
    }


def _aggregate(contribs: list[dict], target_obs: int) -> dict | None:
    """加权平均 → [-100,+100]，附 coverage/confidence/evidence。"""
    if not contribs:
        return None
    total_w = sum(c["w"] for c in contribs)
    score = 100.0 * sum(c["signed"] for c in contribs) / total_w if total_w else 0.0
    score = max(-100.0, min(100.0, round(score, 2)))
    mean_conf = sum(c["confidence"] * c["w"] for c in contribs) / total_w if total_w else 0.0
    coverage = min(1.0, len(contribs) / target_obs)
    sources = {c["event_id"] for c in contribs}
    ranked = sorted(contribs, key=lambda c: abs(c["signed"]), reverse=True)
    return {
        "sentiment_score": score,
        "confidence": round(min(mean_conf * max(coverage, 0.3), 1.0), 4),
        "coverage": round(coverage, 4),
        "observation_count": len(contribs),
        "source_count": len(sources),
        "evidence_ids": [c["analysis_id"] for c in ranked[:WEIGHTS_V1["top_evidence"]]],
        "components": {
            "by_direction": {
                d: sum(1 for c in contribs if c["direction"] == d)
                for d in ("POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED")
            },
            "top_contributors": [
                {"event_id": c["event_id"], "title": c["title"][:80],
                 "direction": c["direction"], "signed": round(c["signed"], 4),
                 "decay": c["decay"], "confidence": c["confidence"]}
                for c in ranked[:5]
            ],
        },
    }


def rebuild(lookback_hours: int = FACTOR_LOOKBACK_HOURS, progress=None) -> dict:
    """重建情绪因子快照：1 个 MARKET + 每个有观测的池内资产 1 个 ASSET。"""
    asof = datetime.now(_TZ)
    since = asof - timedelta(hours=lookback_hours)
    events = repo.analyzed_events_since(since)
    # 经济日历事件带重要性 → 从 raw_extra 提升权重（通过 event_id 关联查一次）
    importance_map = _importance_map(events)
    contribs = []
    for event in events:
        importance = importance_map.get(event["event_id"])
        contribs.append(_event_contribution(event, asof, importance))
    if not contribs:
        return {"asof": asof.isoformat(), "market": None, "assets": {},
                "note": "窗口内无已分析事件，未生成快照"}

    # ---- Market Sentiment：总体风险偏好（版本化符号映射）----
    risk_sign = WEIGHTS_V1["risk_appetite_sign"]
    market_contribs = []
    for c in contribs:
        classes = c["classes"] or []
        if not classes:
            continue
        best = max(
            (risk_sign.get(k, 0.0) * WEIGHTS_V1["market_class_weight"].get(k, 0.5) for k in classes),
            key=abs, default=0.0,
        )
        if abs(best) < 1e-9:
            continue
        mc = dict(c)
        mc["signed"] = c["signed"] * best
        mc["w"] = c["w"] * abs(best)
        market_contribs.append(mc)
    market = _aggregate(market_contribs, WEIGHTS_V1["market_target_observations"])

    # ---- Asset Sentiment：每资产直接点名 + 类别关联 ----
    assets: dict[str, dict] = {}
    for symbol, klass in SYMBOL_CLASS.items():
        linked = []
        for c in contribs:
            direct = symbol in c["symbols"] or symbol in c["event_asset_scope"]
            via_class = klass in c["classes"]
            if direct:
                lc = dict(c)
                lc["w"] = c["w"] * WEIGHTS_V1["direct_link_weight"]
                lc["signed"] = c["signed"] * WEIGHTS_V1["direct_link_weight"]
                linked.append(lc)
            elif via_class:
                lc = dict(c)
                lc["w"] = c["w"] * WEIGHTS_V1["class_link_weight"]
                lc["signed"] = c["signed"] * WEIGHTS_V1["class_link_weight"]
                linked.append(lc)
        agg = _aggregate(linked, WEIGHTS_V1["asset_target_observations"])
        if agg:
            assets[symbol] = agg

    breakdown_weights = {"weights": WEIGHTS_V1, "lookback_hours": lookback_hours,
                         "events_in_window": len(contribs)}
    if market:
        market_row = _score_row("MARKET", None, asof, market, breakdown_weights)
        repo.save_score(market_row)
    asset_rows = []
    for symbol, agg in assets.items():
        row = _score_row("ASSET", symbol, asof, agg, breakdown_weights)
        repo.save_score(row)
        asset_rows.append(row["score_id"])
    if progress:
        progress(f"快照完成：market={'有' if market else '无'}，assets={len(asset_rows)}")
    return {
        "asof": asof.isoformat(), "sentiment_version": SENTIMENT_VERSION,
        "lookback_hours": lookback_hours, "events_in_window": len(contribs),
        "market": {k: market[k] for k in ("sentiment_score", "confidence", "coverage",
                                          "observation_count", "source_count")} if market else None,
        "assets": {
            sym: {k: agg[k] for k in ("sentiment_score", "confidence", "coverage",
                                      "observation_count", "source_count")}
            for sym, agg in assets.items()},
    }


def _importance_map(events: list[dict]) -> dict[str, int]:
    """经济日历事件的重要性（从 news raw_extra 提取，确定性）。"""
    ids = [e["event_id"] for e in events]
    if not ids:
        return {}
    from trading.wealth_lab.sentiment.repository import connect, _LOCK
    marks = ",".join("?" for _ in ids)
    with _LOCK, connect() as con:
        rows = con.execute(
            f"""SELECT event_id, raw_extra FROM wealth_news_items
                WHERE event_id IN ({marks}) AND source_name='baidu-eco-calendar'""",
            ids,
        ).fetchall()
    out = {}
    for event_id, extra_json in rows:
        try:
            extra = json.loads(extra_json or "{}")
            imp = int(extra.get("importance") or 0)
            if imp:
                out[event_id] = imp
        except Exception:
            continue
    return out


def _score_row(level: str, symbol: str | None, asof: datetime, agg: dict,
               breakdown: dict) -> dict:
    return {
        "score_id": "wscore_" + uuid.uuid4().hex[:14],
        "level": level, "symbol": symbol, "asof": asof,
        "sentiment_score": agg["sentiment_score"], "confidence": agg["confidence"],
        "coverage": agg["coverage"], "observation_count": agg["observation_count"],
        "source_count": agg["source_count"], "sentiment_version": SENTIMENT_VERSION,
        "evidence_ids": json.dumps(agg["evidence_ids"], ensure_ascii=False),
        "component_breakdown": json.dumps(
            {**agg["components"], **breakdown}, ensure_ascii=False),
        "created_at": datetime.now(_TZ),
    }
