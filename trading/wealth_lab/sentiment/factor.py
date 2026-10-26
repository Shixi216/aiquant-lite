"""确定性情绪聚合 v1.1：Market Sentiment + Asset Sentiment 两层。

v1.1 变更（相对 v1.0）：
- 资产关联分级加权：EXACT=1.0（直接证据）> SAME_INDEX/MODEL_INFERRED=0.35
  > CLASS_ONLY=0.25；池外产品新闻永不产生直接证据；
- 事件级归并后，一个事实事件只计一次（媒体报道数不再放大权重）；
- 新增证据可信度字段：evidence_basis / evidence_count / direct_evidence_count /
  class_evidence_count / coverage_ratio / last_evidence_at；
- 仅类别推导的个券情绪 confidence 封顶 class_only_confidence_cap（0.6）。

由确定性程序聚合；LLM 只提供单事件结构化判断。权重 WEIGHTS_V1_1 全量随快照
入库可查，版本 SENTIMENT_VERSION=wealth-sentiment/1.1（v1.0 快照保留可对比）。
情绪分数是研究指标：-100~+100，不等于预期收益方向，不构成统计预测概率。
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone

from trading.wealth_lab.sentiment import repository as repo
from trading.wealth_lab.sentiment.config import (
    FACTOR_LOOKBACK_HOURS, SENTIMENT_VERSION, SYMBOL_CLASS, WEIGHTS_V1, WEIGHTS_V1_1,
)

_TZ = timezone(timedelta(hours=8))


def _decay(published_at: datetime, asof: datetime, raw_category: str | None) -> float:
    half_life = WEIGHTS_V1_1["half_life_hours"].get(raw_category or "GENERAL", 48.0)
    age_hours = max((asof - published_at).total_seconds() / 3600.0, 0.0)
    return math.exp(-math.log(2.0) * age_hours / half_life)


def _direction_value(direction: str | None) -> float:
    return {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0,
            "MIXED": WEIGHTS_V1_1["mixed_direction_scale"]}.get(direction or "", 0.0)


def _event_contribution(event: dict, asof: datetime, importance: int | None = None) -> dict:
    """单事件基础贡献（不含资产关联层级）。一个事件 = 一次观测（v1.1 归并后）。"""
    impact = event.get("estimated_impact") or "MINOR"
    weight = WEIGHTS_V1_1["impact"].get(impact, 0.4)
    if importance is not None:
        weight = WEIGHTS_V1_1["eco_importance"].get(importance, weight)
    decay = _decay(event["published_at"], asof, event.get("raw_category"))
    diversity = min(
        WEIGHTS_V1_1["source_diversity_cap"],
        1.0 + WEIGHTS_V1_1["source_diversity_step"] * (max(event.get("source_count", 1), 1) - 1),
    )
    confidence = min(max(float(event.get("confidence") or 0.0), 0.0), 1.0)
    value = _direction_value(event.get("sentiment_direction"))
    try:
        event_links = json.loads(event.get("event_asset_links") or "{}")
    except Exception:
        event_links = {}
    return {
        "analysis_id": event["analysis_id"], "event_id": event["event_id"],
        "title": event["title"], "published_at": event["published_at"],
        "direction": event.get("sentiment_direction"), "impact": impact,
        "classes": json.loads(event.get("affected_asset_classes") or "[]"),
        "analysis_symbols": json.loads(event.get("affected_symbols") or "[]"),
        "event_links": event_links,
        "confidence": confidence,
        "w": weight * decay * diversity,
        "signed": value * weight * decay * diversity * confidence,
        "decay": round(decay, 4),
    }


def _symbol_link_level(contrib: dict, symbol: str) -> str | None:
    """事件→资产 的关联层级（确定性优先，LLM 推断兜底为 MODEL_INFERRED）。"""
    info = contrib["event_links"].get(symbol)
    if isinstance(info, dict) and info.get("level") in WEIGHTS_V1_1["link_weight"]:
        if info["level"] != "CLASS_ONLY":
            return info["level"]
    if symbol in contrib["analysis_symbols"]:
        return "MODEL_INFERRED"
    return None


def _aggregate(contribs: list[dict], target_obs: int, *, direct_levels=("EXACT",),
               class_only_confidence_cap: float | None = None) -> dict | None:
    """加权平均 → [-100,+100]，附证据分层统计。

    contribs 的每一项需带 "link_level"（资产层调用方填充；市场层不填=全部同权）。
    """
    if not contribs:
        return None
    total_w = sum(c["w"] for c in contribs)
    score = 100.0 * sum(c["signed"] for c in contribs) / total_w if total_w else 0.0
    score = max(-100.0, min(100.0, round(score, 2)))
    mean_conf = sum(c["confidence"] * c["w"] for c in contribs) / total_w if total_w else 0.0
    coverage = min(1.0, len(contribs) / target_obs)
    direct_count = sum(1 for c in contribs if c.get("link_level") in direct_levels)
    confidence = min(mean_conf * max(coverage, 0.3), 1.0)
    if class_only_confidence_cap is not None and direct_count == 0:
        confidence = min(confidence, class_only_confidence_cap)
    ranked = sorted(contribs, key=lambda c: abs(c["signed"]), reverse=True)
    return {
        "sentiment_score": score,
        "confidence": round(confidence, 4),
        "coverage": round(coverage, 4),
        "observation_count": len(contribs),
        "source_count": len({c["event_id"] for c in contribs}),
        "direct_evidence_count": direct_count,
        "class_evidence_count": len(contribs) - direct_count,
        "last_evidence_at": max(c["published_at"] for c in contribs).isoformat(),
        "evidence_ids": [c["analysis_id"] for c in ranked[:WEIGHTS_V1_1["top_evidence"]]],
        "components": {
            "by_direction": {
                d: sum(1 for c in contribs if c["direction"] == d)
                for d in ("POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED")
            },
            "by_link_level": {
                lv: sum(1 for c in contribs if c.get("link_level") == lv)
                for lv in ("EXACT", "SAME_INDEX", "MODEL_INFERRED", "CLASS_ONLY")
            },
            "top_contributors": [
                {"event_id": c["event_id"], "title": c["title"][:80],
                 "direction": c["direction"], "signed": round(c["signed"], 4),
                 "link_level": c.get("link_level"),
                 "decay": c["decay"], "confidence": c["confidence"]}
                for c in ranked[:5]
            ],
        },
    }


def rebuild(lookback_hours: int = FACTOR_LOOKBACK_HOURS, progress=None,
            skip_maintenance: bool = False) -> dict:
    """重建情绪因子快照：1 个 MARKET + 每个有观测的池内资产 1 个 ASSET。

    重建前自动执行确定性维护（存量重分级 + 事件归并），幂等、无 LLM 调用。
    """
    maintenance = {"relinked": None, "merged": None}
    if not skip_maintenance:
        from trading.wealth_lab.sentiment import repository as repo_mod
        from trading.wealth_lab.sentiment.merge import merge_existing_events
        relink = repo_mod.recompute_all_links(progress=progress)
        merged = merge_existing_events(progress=progress)
        maintenance = {"relinked": relink, "merged": merged.get("merged", 0)}
        if progress:
            progress(f"维护完成：重分级 {relink['items_relinked']} 条目，"
                     f"归并 {merged.get('merged', 0)} 个重复事件")

    asof = datetime.now(_TZ)
    since = asof - timedelta(hours=lookback_hours)
    events = repo.analyzed_events_since(since)
    importance_map = _importance_map(events)
    contribs = [_event_contribution(e, asof, importance_map.get(e["event_id"]))
                for e in events]
    if not contribs:
        return {"asof": asof.isoformat(), "sentiment_version": SENTIMENT_VERSION,
                "maintenance": maintenance, "market": None, "assets": {},
                "zero_observation_assets": sorted(SYMBOL_CLASS),
                "note": "窗口内无已分析事件，未生成快照"}

    # ---- Market Sentiment：总体风险偏好（版本化符号映射；事件已归并去重）----
    risk_sign = WEIGHTS_V1_1["risk_appetite_sign"]
    market_contribs = []
    for c in contribs:
        classes = c["classes"] or []
        if not classes:
            continue
        best = max(
            (risk_sign.get(k, 0.0) * WEIGHTS_V1_1["market_class_weight"].get(k, 0.5) for k in classes),
            key=abs, default=0.0,
        )
        if abs(best) < 1e-9:
            continue
        mc = dict(c)
        mc["signed"] = c["signed"] * best
        mc["w"] = c["w"] * abs(best)
        market_contribs.append(mc)
    market = _aggregate(market_contribs, WEIGHTS_V1_1["market_target_observations"])
    if market:
        # 市场层观测本身即事件级直接证据（非个券推导），计数保持自洽
        market["evidence_basis"] = "DIRECT_EVIDENCE"
        market["evidence_count"] = market["observation_count"]
        market["direct_evidence_count"] = market["observation_count"]
        market["class_evidence_count"] = 0

    # ---- Asset Sentiment：分层关联（EXACT 直接证据；其余为间接、低权重）----
    link_w = WEIGHTS_V1_1["link_weight"]
    assets: dict[str, dict] = {}
    for symbol, klass in SYMBOL_CLASS.items():
        linked = []
        for c in contribs:
            level = _symbol_link_level(c, symbol)
            if level is None and klass in c["classes"]:
                level = "CLASS_ONLY"
            if level is None:
                continue
            lc = dict(c)
            lc["link_level"] = level
            factor = link_w.get(level, 0.25)
            lc["w"] = c["w"] * factor
            lc["signed"] = c["signed"] * factor
            linked.append(lc)
        agg = _aggregate(
            linked, WEIGHTS_V1_1["asset_target_observations"],
            class_only_confidence_cap=WEIGHTS_V1_1["class_only_confidence_cap"],
        )
        if agg:
            direct = agg["direct_evidence_count"]
            agg["evidence_basis"] = (
                "DIRECT_EVIDENCE"
                if direct >= WEIGHTS_V1_1["min_direct_obs_for_direct_basis"]
                else "CLASS_LEVEL_EVIDENCE"
            )
            agg["evidence_count"] = agg["observation_count"]
            assets[symbol] = agg

    breakdown_weights = {
        "weights": WEIGHTS_V1_1,
        "v1_0_weights_for_reference": {
            "direct_link_weight": WEIGHTS_V1["direct_link_weight"],
            "class_link_weight": WEIGHTS_V1["class_link_weight"],
        },
        "lookback_hours": lookback_hours,
        "events_in_window": len(contribs),
    }
    if market:
        repo.save_score(_score_row("MARKET", None, asof, market, breakdown_weights))
    for symbol, agg in assets.items():
        repo.save_score(_score_row("ASSET", symbol, asof, agg, breakdown_weights))
    if progress:
        progress(f"快照完成：market={'有' if market else '无'}，assets={len(assets)}")

    direct_based = {s for s, a in assets.items() if a["evidence_basis"] == "DIRECT_EVIDENCE"}
    return {
        "asof": asof.isoformat(), "sentiment_version": SENTIMENT_VERSION,
        "maintenance": maintenance, "lookback_hours": lookback_hours,
        "events_in_window": len(contribs),
        "market": {k: market[k] for k in (
            "sentiment_score", "confidence", "coverage", "observation_count",
            "source_count")} if market else None,
        "assets": {
            sym: {k: agg[k] for k in (
                "sentiment_score", "confidence", "coverage", "observation_count",
                "source_count", "evidence_basis", "direct_evidence_count",
                "class_evidence_count", "last_evidence_at")}
            for sym, agg in assets.items()},
        "zero_observation_assets": sorted(set(SYMBOL_CLASS) - set(assets)),
        "direct_evidence_assets": sorted(direct_based),
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
        "evidence_basis": agg.get("evidence_basis"),
        "evidence_count": agg.get("evidence_count"),
        "direct_evidence_count": agg.get("direct_evidence_count"),
        "class_evidence_count": agg.get("class_evidence_count"),
        "last_evidence_at": (datetime.fromisoformat(agg["last_evidence_at"])
                             if agg.get("last_evidence_at") else None),
        "created_at": datetime.now(_TZ),
    }
