"""Wealth Sentiment 编排入口：供 v3.2 任务控制中心 handler 调用（真实执行）。"""
from __future__ import annotations

from trading.wealth_lab.sentiment import factor, ingest
from trading.wealth_lab.sentiment.config import (
    ANALYSIS_BATCH_LIMIT, ANALYSIS_BATCH_LIMIT_MAX, FACTOR_LOOKBACK_HOURS, SOURCES,
)


def refresh_news(progress=None, sources: list[str] | None = None,
                 fund_symbols: list[str] | None = None) -> dict:
    """真实采集：每个启用源一次实际调用，逐源记录运行状态。绝不返回假成功。"""
    runs = ingest.refresh(sources=sources, fund_symbols=fund_symbols, progress=progress)
    summary = []
    for run in runs:
        summary.append({
            "source_name": run["source_name"], "status": run["status"],
            "items_fetched": run["items_fetched"], "items_new": run["items_new"],
            "items_duplicate": run["items_duplicate"], "items_skipped": run["items_skipped"],
            "field_completeness": run["field_completeness"],
            "latest_published_at": run["latest_published_at"].isoformat()
            if run["latest_published_at"] else None,
            "error": run["error"],
        })
    failed = [s["source_name"] for s in summary if s["status"] == "FAILED"]
    return {
        "sources": summary,
        "total_new": sum(s["items_new"] for s in summary),
        "failed_sources": failed,
        "ok": len(failed) < len(summary) if summary else False,
    }


def analyze_sentiment(progress=None, limit: int | None = None) -> dict:
    """批量 LLM 分析 PENDING 事件（deepseek-flash 优先，qwen 兜底）。"""
    from trading.wealth_lab.sentiment.analyzer import analyze_batch
    batch = max(1, min(int(limit or ANALYSIS_BATCH_LIMIT), ANALYSIS_BATCH_LIMIT_MAX))
    results = analyze_batch(batch, progress=progress)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"batch_limit": batch, "analyzed": len(results),
            "by_status": by_status, "results": results}


def rebuild_factor(progress=None, lookback_hours: int | None = None) -> dict:
    """确定性聚合：重建 Market + Asset 情绪快照。"""
    hours = int(lookback_hours or FACTOR_LOOKBACK_HOURS)
    return factor.rebuild(lookback_hours=hours, progress=progress)


def source_registry() -> list[dict]:
    """源注册表 + 最近运行统计（供 API /sources）。"""
    from trading.wealth_lab.sentiment import repository as repo
    stats = {s["source_name"]: s for s in repo.source_stats()}
    out = []
    for name, meta in SOURCES.items():
        stat = stats.get(name, {})
        out.append({
            "source_name": name, "provider": meta["provider"],
            "enabled": meta["enabled"], "description": meta["description"],
            "doc": meta["doc"],
            "runs": stat.get("runs", 0),
            "ok_runs": stat.get("ok_runs", 0),
            "last_run_at": stat.get("last_run_at").isoformat() if stat.get("last_run_at") else None,
            "avg_field_completeness": stat.get("avg_field_completeness"),
            "total_new": stat.get("total_new", 0),
            "latest_published_at": stat.get("latest_published_at").isoformat()
            if stat.get("latest_published_at") else None,
        })
    return out
