"""任务操作实现（白名单 8 种）：复用 DualSourceProvider / 审计 / 引擎 / AI，禁止第三套采集器。"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

from trading.wealth_lab.jobs import Job, JobManager

_TZ = timezone(timedelta(hours=8))


def _pool_symbols(params: dict) -> list[str]:
    """数据任务资产范围：显式 symbols（必须已在池内）或整个研究池。"""
    from trading.wealth_lab.universe import assets_report
    known = {a["symbol"] for a in assets_report()}
    symbols = params.get("symbols")
    if symbols:
        invalid = [s for s in symbols if s not in known]
        if invalid:
            raise ValueError(f"资产不在研究池白名单内: {invalid}")
        return list(symbols)
    return sorted(known)


def execute(mgr: JobManager, job: Job) -> None:
    handler = {
        "update_fund_basic": _update_fund_basic,
        "update_market_data": _update_market_data,
        "update_adj_factors": _update_adj_factors,
        "run_quality_check": _run_quality_check,
        "rebuild_pit": _rebuild_pit,
        "run_backtest": _run_backtest,
        "run_ai_research": _run_ai_research,
        "generate_report": _generate_report,
        "refresh_wealth_news": _refresh_wealth_news,
        "analyze_wealth_sentiment": _analyze_wealth_sentiment,
        "rebuild_wealth_sentiment_factor": _rebuild_wealth_sentiment_factor,
    }[job.operation]
    handler(mgr, job)


def _update_fund_basic(mgr: JobManager, job: Job) -> None:
    from trading.wealth_lab.provider import OfficialTushareProvider
    from trading.wealth_lab.universe import assets_report, upsert_assets
    mgr.update(job, 10, "拉取官方 fund_basic 名录（E+O）")
    official = OfficialTushareProvider()
    registry = {}
    for market in ("E", "O"):
        frame = official._call("fund_basic", market=market, limit=3000)
        for row in frame.itertuples(index=False):
            registry[str(row.ts_code)] = row
        time.sleep(0.3)
    job.source = "official-tushare/fund_basic"
    rows = assets_report()
    updated = 0
    for i, a in enumerate(rows):
        meta = registry.get(a["symbol"])
        if meta is None:
            continue
        try:
            a["list_date"] = date.fromisoformat(str(meta.list_date)[:10]) if meta.list_date else a.get("list_date")
            a["delist_date"] = date.fromisoformat(str(meta.delist_date)[:10]) if meta.delist_date else a.get("delist_date")
        except Exception:
            pass
        if a["name"] != str(meta.name):
            a["notes"] = (a.get("notes", "") + f"；名称更新（{a['name']}→{str(meta.name)}）").strip("；")
            a["name"] = str(meta.name)
        updated += 1
        if not mgr.update(job, 10 + int(80 * (i + 1) / len(rows)), f"核对 {a['symbol']}"):
            return
    upsert_assets(rows)
    job.asset_scope = [a["symbol"] for a in rows]
    mgr.update(job, 100, f"完成：{updated} 只元数据核对/更新")
    job.result_ref = {"updated": updated, "total": len(rows)}


def _update_market_data(mgr: JobManager, job: Job) -> None:
    from trading.wealth_lab.provider import DualSourceProvider
    from trading.wealth_lab.repository import WealthLabRepository
    from trading.wealth_lab.universe import assets_report
    symbols = _pool_symbols(job.params)
    job.asset_scope = symbols
    end = job.params.get("end_date") or date.today().strftime("%Y%m%d")
    dual = DualSourceProvider()
    repo = WealthLabRepository()
    report = assets_report(symbols)
    coverage = {a["symbol"]: a for a in report}
    mgr.update(job, 5, f"增量更新开始：{len(symbols)} 只，截至 {end}")
    total_rows = 0
    for i, symbol in enumerate(symbols):
        last = coverage.get(symbol, {}).get("data_last")
        start = job.params.get("start_date") or (
            (date.fromisoformat(last) + timedelta(days=1)).strftime("%Y%m%d") if last else "20200101"
        )
        frame, source_status = dual.fetch_fund_daily(symbol, start, end)
        written = repo.upsert_daily(frame, symbol)
        total_rows += written
        if source_status:
            existing = {part for part in (job.source or "").split("/") if part}
            merged = existing | {f"{k}:{v}" for k, v in source_status.items()}
            job.source = "/".join(sorted(merged))
        if not mgr.update(job, 5 + int(90 * (i + 1) / len(symbols)),
                          f"{symbol}: +{written} 行（{start}~{end}）", records=written):
            mgr.update(job, log="取消：已完成部分已入库（幂等，可重跑）")
            return
        time.sleep(0.3)
    mgr.update(job, 100, f"完成：共写入 {total_rows} 行")
    job.result_ref = {"rows": total_rows, "symbols": len(symbols)}


def _update_adj_factors(mgr: JobManager, job: Job) -> None:
    """复权因子单独重拉：缺失即失败（不填充），与行情更新同一审计口径。"""
    _update_market_data(mgr, job)  # fund_adj 在 fetch_fund_daily 内强制校验缺失
    job.result_ref = {**job.result_ref, "note": "复权因子随行情一起校验；缺失即任务失败"}


def _run_quality_check(mgr: JobManager, job: Job) -> None:
    from trading.wealth_lab.universe import assets_report, upsert_assets
    import duckdb as _duckdb
    from trading.wealth_lab.config import WEALTH_LAB_DB_PATH
    mgr.update(job, 10, "以池内真实交易日历重算缺失率与质量状态")
    con = _duckdb.connect(str(WEALTH_LAB_DB_PATH), read_only=True)
    cal = [r[0] for r in con.execute(
        "SELECT trade_date FROM etf_daily WHERE symbol='510300.SH' ORDER BY trade_date").fetchall()]
    con.close()
    asof = date(2026, 10, 23)
    rows = assets_report()
    before = {a["symbol"]: a["quality_status"] for a in rows}
    for i, a in enumerate(rows):
        # v3.4 修复：assets_report 返回 ISO 字符串（此前被当"无上市日"，
        # 晚上市资产的缺失率分母错用全日历 1648 天 → 5 只被误降级）
        raw_list = a.get("list_date")
        list_date = raw_list if isinstance(raw_list, date) else (
            date.fromisoformat(str(raw_list)[:10])
            if raw_list and str(raw_list)[:4].isdigit() else None)
        start = max(list_date or date(2020, 1, 2), date(2020, 1, 2))
        expected = sum(1 for d in cal if start <= d <= asof)
        valid = a.get("valid_days", 0)
        missing = max(0.0, 1.0 - valid / expected) if expected else 1.0
        a["expected_days"] = expected
        a["missing_ratio"] = round(missing, 4)
        quality = "QUALIFIED" if (
            valid >= 250 and a.get("adj_missing", 0) == 0 and missing <= 0.05
            and (a.get("avg_amount_20d_cny") or 0) >= 2e7
        ) else ("DEGRADED" if valid >= 100 and a.get("adj_missing", 0) == 0 else "REJECTED")
        a["quality_status"] = quality
        if not mgr.update(job, 10 + int(85 * (i + 1) / len(rows)), f"{a['symbol']} → {quality}"):
            return
    upsert_assets(rows)
    counts: dict[str, int] = {}
    for a in rows:
        counts[a["quality_status"]] = counts.get(a["quality_status"], 0) + 1
    mgr.update(job, 100, f"完成：{counts}")
    after = {a["symbol"]: a["quality_status"] for a in rows}
    changes = [{"symbol": sym, "from": before[sym], "to": after[sym]}
               for sym in before if before[sym] != after[sym]]
    job.result_ref = {"quality_counts": counts, "total": len(rows),
                      "changes_vs_before": changes,
                      "rule_version": "quality-rules/1.1 (2026-10-25: ISO list_date fix)"}


def _rebuild_pit(mgr: JobManager, job: Job) -> None:
    from trading.wealth_lab.universe import backtest_eligible, list_universes, universe_members
    mgr.update(job, 10, "对每个资产池的最近 12 个月初时点重评 PIT 资格")
    snapshots = []
    universes = list_universes()
    anchors = []
    today = date(2026, 10, 1)
    for k in range(12):
        y, m = (today.year - 1, today.month + k) if today.month + k <= 12 else (today.year, today.month + k - 12)
        anchors.append(date(y, m, 1))
    for i, u in enumerate(universes):
        members = universe_members(u["universe_id"])
        for anchor in anchors:
            verdict = backtest_eligible(members, anchor)
            eligible = sum(1 for v in verdict.values() if v["eligible"])
            snapshots.append({"universe_id": u["universe_id"], "asof": anchor.isoformat(),
                              "eligible": eligible, "total": len(members)})
        if not mgr.update(job, 10 + int(85 * (i + 1) / len(universes)),
                          f"{u['universe_id']}: 12 个时点重评完成"):
            return
    mgr.update(job, 100, f"完成：{len(snapshots)} 个时点快照")
    job.result_ref = {"snapshots": snapshots[-24:]}


def _run_backtest(mgr: JobManager, job: Job) -> None:
    from trading.wealth_lab.routes_v3 import UniverseBacktestBody, v3_universe_backtest
    mgr.update(job, 20, f"启动回测：{job.params}")
    response = v3_universe_backtest(
        job.params["strategy_id"],
        UniverseBacktestBody(**{k: v for k, v in job.params.items()
                                if k in ("universe_id", "start_date", "end_date", "cost_bps", "params_override")}),
    )
    payload = response["data"]
    if payload.get("data_mode") == "UNAVAILABLE":
        raise RuntimeError(f"回测被拒：{payload.get('reason')}")
    mgr.update(job, 90, f"回测完成：{payload['backtest_id']}")
    job.result_ref = {
        "backtest_id": payload["backtest_id"],
        "universe_id": payload["universe_id"],
        "time_semantics": payload["time_semantics"],
        "metrics": payload["metrics"],
    }
    mgr.update(job, 100, "完成")


def _run_ai_research(mgr: JobManager, job: Job) -> None:
    import asyncio
    from trading.wealth_lab.ai.agents import WealthResearchAgent, WealthRiskReviewer
    from trading.wealth_lab.repository import WealthLabRepository
    backtest_id = job.params.get("backtest_id")
    if not backtest_id:
        raise ValueError("run_ai_research 需要 backtest_id")
    record = WealthLabRepository().load_backtest(backtest_id)
    if record is None:
        raise ValueError(f"backtest not found: {backtest_id}")
    result = record["result"]
    facts = {
        "universe_id": result.get("universe_id"),
        "strategy": result.get("strategy", {}).get("strategy_id"),
        "time_semantics": result.get("time_semantics"),
        "metrics": result.get("metrics"),
        "pit_note": result.get("pit_gate", {}).get("note"),
    }
    mgr.update(job, 30, f"真实模型调用（backtest={backtest_id}）")
    research = asyncio.run(WealthResearchAgent().run(facts))
    mgr.update(job, 70, f"研究完成：{research['research_id']}")
    review = asyncio.run(WealthRiskReviewer().run(research, result.get("metrics", {})))
    mgr.update(job, 95, f"风险复核完成：{review['review_id']} verdict={review['verdict']}")
    from trading.wealth_lab.repository import WealthLabRepository as _Repo
    _Repo().save_research_context(research["research_id"], {
        "backtest_id": backtest_id,
        "universe_id": result.get("universe_id"),
        "strategy_id": result.get("strategy", {}).get("strategy_id"),
        "metrics": result.get("metrics"),
        "research": research, "review": review,
    })
    job.result_ref = {"research_id": research["research_id"], "review_id": review["review_id"],
                      "ai_verdict": review["verdict"]}
    mgr.update(job, 100, "完成（AI 结论为研究辅助，不替代程序计算真实性检查）")


def _generate_report(mgr: JobManager, job: Job) -> None:
    import pandas as pd
    from trading.wealth_lab.backtest import run_strategy_backtest
    from trading.wealth_lab.repository import WealthLabRepository
    from trading.wealth_lab.report import build_report
    from trading.wealth_lab.strategies import get_strategy
    backtest_id = job.params.get("backtest_id")
    record = WealthLabRepository().load_backtest(backtest_id) if backtest_id else None
    if record is None:
        raise ValueError("generate_report 需要 backtest_id")
    result = record["result"]
    params = record["params"]
    strategy = get_strategy(params.get("strategy_id", "S0_fixed_weight"))
    members = list({s for snap in result.get("weight_history", [])
                    for s in snap.get("weights", {})}) or None
    close = WealthLabRepository().read_close_matrix(members or list(result.get("pit_gate", {}).get("eligible", [])))
    output = run_strategy_backtest(
        close, strategy,
        start_date=date.fromisoformat(result["common_window"]["start_date"]),
        end_date=date.fromisoformat(result["common_window"]["end_date"]),
        cost_bps=5.0,
    )
    mgr.update(job, 70, "从存档重放生成报告")
    report = build_report(output, backtest_id)
    mgr.update(job, 100, "完成")
    job.result_ref = {"backtest_id": backtest_id, "metrics": report["metrics"],
                      "years": sorted(report["monthly_returns"])}


# ---------------------------------------------------------------- Wealth Sentiment v1
def _refresh_wealth_news(mgr: JobManager, job: Job) -> None:
    """真实采集多资产资讯（独立库，绝不返回假成功；失败源如实计入结果）。"""
    from trading.wealth_lab.sentiment import service as sentiment_service
    mgr.update(job, 5, "开始真实采集（AKShare 东财/百度公开接口）")
    result = sentiment_service.refresh_news(
        progress=lambda msg: mgr.update(job, log=msg),
        sources=job.params.get("sources"),
        fund_symbols=job.params.get("fund_symbols"),
    )
    job.source = "/".join(s["source_name"] for s in result["sources"])
    total = max(len(result["sources"]), 1)
    job.result_ref = result
    for i, s in enumerate(result["sources"]):
        if not mgr.update(job, 5 + int(90 * (i + 1) / total),
                          f"{s['source_name']}: {s['status']} +{s['items_new']}"
                          f"（字段完整率 {s['field_completeness']}）"):
            return
    if result["failed_sources"] and not result["ok"]:
        raise RuntimeError(f"全部采集源失败: {result['failed_sources']}")
    job.records_written = result["total_new"]
    mgr.update(job, 100, f"完成：新增 {result['total_new']} 条，失败源 {result['failed_sources'] or '无'}")


def _analyze_wealth_sentiment(mgr: JobManager, job: Job) -> None:
    """批量 LLM 结构化分析（deepseek-flash 优先，qwen 兜底，每事件一次调用）。"""
    from trading.wealth_lab.sentiment import service as sentiment_service
    mgr.update(job, 10, f"取 PENDING 事件，batch={job.params.get('limit', 8)}")
    result = sentiment_service.analyze_sentiment(
        progress=lambda msg: mgr.update(job, log=msg),
        limit=job.params.get("limit"),
    )
    by_status = result["by_status"]
    total = result["analyzed"]
    mgr.update(job, 90, f"批次完成：{by_status}")
    job.result_ref = {k: v for k, v in result.items() if k != "results"}
    job.result_ref["event_results"] = result["results"]
    job.source = "router/deepseek-flash+qwen-fallback"
    mgr.update(job, 100, f"完成：分析 {total} 个事件 {by_status}")


def _rebuild_wealth_sentiment_factor(mgr: JobManager, job: Job) -> None:
    """确定性聚合重建情绪因子快照（无 LLM 调用）。"""
    from trading.wealth_lab.sentiment import service as sentiment_service
    lookback = job.params.get("lookback_hours", 168)
    mgr.update(job, 20, f"确定性聚合：窗口 {lookback}h")
    result = sentiment_service.rebuild_factor(
        progress=lambda msg: mgr.update(job, log=msg),
        lookback_hours=lookback,
    )
    job.result_ref = result
    job.source = "deterministic-aggregation/" + result.get("sentiment_version", "")
    job.records_written = (1 if result.get("market") else 0) + len(result.get("assets") or {})
    mgr.update(job, 100, "完成（研究因子，不触碰策略与风控）")
