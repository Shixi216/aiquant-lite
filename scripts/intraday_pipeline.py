"""今日查询统一流程（两阶段 + 双时间轴 + 明确动作）— 2026-08 实时路由修复

流程：
阶段1 盘中候选：实时确定性指标筛选 20-50 只（网络0、模型0）
阶段2 候选研究：日线/基本面/公告/新闻/风控 → formal_score + 盘中强度 + 波段质量
输出：数据时间 / 市场概况 / 候选列表 / 正式研究 / 明确动作

正式方向：formal_score = 技术×0.6 + 基本×0.4（不修改）
情绪/政策/资金只调置信度/仓位/节奏/风险
硬VETO 最高优先级
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_hub.services.market_realtime_snapshot import MarketRealtimeSnapshotService
from data_hub.services.trading_calendar_service import TradingCalendarService
from trading.scanner.query_router import (
    AnalysisHorizon, DualTimeline, TwoStagePipeline, route_query,
)

TZ = timezone(timedelta(hours=8))


class IntradayPipeline:
    """今日查询两阶段流程"""

    def __init__(self, realtime_service: Optional[MarketRealtimeSnapshotService] = None):
        self.realtime = realtime_service or MarketRealtimeSnapshotService()
        self.pipeline = TwoStagePipeline(self.realtime)

    # ------------------------------------------------------------------
    # 阶段1：盘中候选（实时确定性指标）
    # ------------------------------------------------------------------
    def stage1_candidates(self, max_candidates: int = 50) -> tuple[list[dict], dict]:
        quotes, meta = self.realtime.get_snapshot()
        # 首次/STALE且无有效快照：同步刷新一次（仅首次，之后后台维护）
        if not quotes or meta["status"] == "STALE_REALTIME":
            if not quotes:  # 完全无快照 → 同步拉取（首次冷启动）
                self.realtime.refresh_once()
            quotes, meta = self.realtime.get_snapshot()
        cands = self.pipeline.generate_intraday_candidates(quotes, max_candidates=max_candidates)
        return cands, meta

    # ------------------------------------------------------------------
    # 阶段2：候选研究（日线/基本面/风控）
    # ------------------------------------------------------------------
    def stage2_research(self, candidates: list[dict], limit: int = 15) -> list[dict]:
        """对候选调用五维研究（技术/基本为正式分，情绪/政策/资金为增强）"""
        results = []
        for c in candidates[:limit]:
            symbol = c["symbol"].split(".")[0]
            try:
                research = self._research_one(symbol, c)
                results.append(research)
            except Exception as exc:
                results.append({
                    "symbol": c["symbol"], "name": c["name"], "error": str(exc)[:100],
                    "status": "ERROR",
                })
        return results

    def _research_one(self, symbol: str, realtime: dict) -> dict:
        """单只研究：五维因子 + 盘中强度 + 波段质量 + 候选级VETO"""
        import duckdb
        from trading.research.technical.analysis import technical_signal
        from scripts.analyze_stock import (
            load_bars, load_financials, load_news, fundamental_factor,
        )
        from trading.decision_support.candidate_veto import CandidateVetoService

        db_path = Path(__file__).resolve().parents[1] / "database" / "hermes_opc.duckdb"
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            bars = load_bars(con, symbol)
            fin = load_financials(con, symbol)
            news = load_news(con, symbol)
        finally:
            con.close()

        # 候选级VETO：数据完整性 + 新闻/公告关键词
        veto_svc = CandidateVetoService()
        veto_data = veto_svc.check_data_availability(bool(bars), bool(fin))
        if veto_data.vetoed:
            return {**realtime, "status": "VETO", "veto": veto_data.veto_type,
                    "veto_reason": veto_data.reason}

        # 新闻标题VETO检测
        news_titles = [n.get("title", "") if isinstance(n, dict) else str(n) for n in (news or [])]
        veto_news = veto_svc.check_news_list(news_titles, source="新闻")
        if veto_news.vetoed:
            return {**realtime, "status": "VETO", "veto": veto_news.veto_type,
                    "veto_reason": veto_news.reason, "veto_evidence": veto_news.matched_keywords}

        if not bars:
            return {**realtime, "status": "NO_BARS", "error": "无日线数据"}

        tech = technical_signal(bars)
        fund = fundamental_factor(fin)
        # Signal 对象用属性访问（.score/.confidence）
        tech_score = tech.score if hasattr(tech, "score") else 0
        tech_conf = tech.confidence if hasattr(tech, "confidence") else 0.5
        fund_score = fund.score if hasattr(fund, "score") else 0
        fund_conf = fund.confidence if hasattr(fund, "confidence") else 0.5
        formal = tech_score * 0.6 + fund_score * 0.4

        # 盘中强度（实时）
        intraday = self.pipeline.intraday_strength(realtime)
        # 波段质量（日线+基本面）
        swing = self.pipeline.swing_quality(tech_score, fund_score, formal)

        return {
            **realtime,
            "status": "OK",
            "technical_score": round(tech_score, 3),
            "fundamental_score": round(fund_score, 3),
            "formal_score": round(formal, 3),
            "confidence": round(tech_conf * 0.6 + fund_conf * 0.4, 3),
            "intraday_strength": intraday,
            "swing_quality": swing,
            "veto": "NO_VETO",  # 候选级通过（完整VETO在DECISION层）
        }

    # ------------------------------------------------------------------
    # 候选级动作（INTRADAY_SCAN 输出，非正式交易动作）
    # ------------------------------------------------------------------
    @staticmethod
    def candidate_action_for(research: dict) -> dict:
        """候选级动作（规则二：盘中扫描不得直接输出正式建仓结论）

        允许：强势研究候选 / 等待确认 / 禁止追高 / 暂不参与 / 风险待核验
        正式交易动作（建仓/加仓/减仓等）必须进入DECISION后才输出。
        """
        # 候选级VETO命中 → 风险待核验
        if research.get("status") == "VETO":
            return {"action": "风险待核验", "action_code": "VETO_PENDING",
                    "veto_type": research.get("veto", ""),
                    "reason": research.get("veto_reason", "候选级VETO命中")}

        # 数据不足 → 暂不参与
        if research.get("status") != "OK":
            return {"action": "暂不参与", "action_code": "SKIP",
                    "reason": research.get("error", "数据不足")}

        formal = research.get("formal_score", 0)
        intra = research.get("intraday_strength", {})
        swing = research.get("swing_quality", {})
        intra_level = intra.get("level", "弱")
        swing_level = swing.get("level", "中性")

        # 盘中强 + 波段强 + 正式正 → 强势研究候选（需DECISION确认才能建仓）
        if intra_level in ("极强", "强") and swing_level in ("强", "中等") and formal >= 0.2:
            return {"action": "强势研究候选", "action_code": "CANDIDATE",
                    "reason": "今日强势且中期结构支持，建议进入DECISION正式评估"}

        # 盘中暴涨但正式负 → 禁止追高
        if intra_level in ("极强", "强") and formal < 0:
            return {"action": "禁止追高", "action_code": "NO_CHASE",
                    "reason": "盘中强但正式评分负向，等结构确认"}

        # 盘中强 + 波段弱 → 禁止追高
        if intra_level in ("极强", "强") and swing_level in ("弱", "差"):
            return {"action": "禁止追高", "action_code": "NO_CHASE",
                    "reason": "短线异动但中期趋势未反转"}

        # 正式正 + 盘中弱 → 等待确认
        if formal >= 0.2 and intra_level == "弱":
            return {"action": "等待确认", "action_code": "WAIT",
                    "reason": "中期质量好但今日缺少触发"}

        return {"action": "暂不参与", "action_code": "SKIP", "reason": "信号不足"}

    # ------------------------------------------------------------------
    # 正式交易动作（仅DECISION层）
    # ------------------------------------------------------------------
    @staticmethod
    def formal_action_only() -> dict:
        """说明：正式交易动作（建仓/小仓试错/加仓/持有/减仓/止盈/止损/清仓/回避）
        必须通过 DecisionEngine 完成：数据状态检查 → point-in-time → 正式60/40
        → 完整VETO → 用户持仓 → PositionSizer 后才输出。"""
        return {
            "note": "正式交易动作仅由DECISION层输出（DecisionEngine），"
                    "INTRADAY_SCAN只输出候选级动作。",
            "allowed_in_intraday": False,
        }

    # ------------------------------------------------------------------
    # 市场概况（实时）
    # ------------------------------------------------------------------
    def market_overview(self, quotes: list) -> dict:
        up = sum(1 for q in quotes if q.pct_chg > 0)
        down = sum(1 for q in quotes if q.pct_chg < 0)
        limit_up = sum(1 for q in quotes if q.pct_chg >= 9.8)
        limit_down = sum(1 for q in quotes if q.pct_chg <= -9.8)
        total = len(quotes) or 1
        strength = "强" if up / total > 0.6 else ("弱" if up / total < 0.4 else "中性")
        return {
            "上涨家数": up, "下跌家数": down, "涨停家数": limit_up, "跌停家数": limit_down,
            "市场强弱": strength, "总样本": len(quotes),
        }

    # ------------------------------------------------------------------
    # 统一输出
    # ------------------------------------------------------------------
    def run(self, max_research: int = 15, verbose: bool = True) -> dict:
        t0 = time.perf_counter()
        cands, meta = self.stage1_candidates()
        t1 = time.perf_counter()

        research = self.stage2_research(cands, limit=max_research)
        t2 = time.perf_counter()

        # 统计
        ok = [r for r in research if r.get("status") == "OK"]
        vetoed = [r for r in research if r.get("status") == "VETO"]
        qualified = [r for r in ok if r.get("formal_score", -1) >= 0.2]
        degraded = [r for r in research if r.get("status") == "DEGRADED"]
        pending = [r for r in research if r.get("status") not in ("OK", "DEGRADED", "VETO")]
        not_researched = max(0, len(cands) - len(research))

        quotes, meta2 = self.realtime.get_snapshot()
        overview = self.market_overview(quotes)

        result = {
            "title": "盘中强势候选",   # 语义修正：非"今日可买股票"
            "data_time": {
                "realtime": meta2.get("snapshot_time", ""),
                "realtime_status": meta2.get("status", ""),
                "realtime_age_seconds": meta2.get("age_seconds"),
                "daily_trade_date": self._latest_completed_trade_date(),
                # 调度统计
                "refresh_started_at": meta2.get("refresh_started_at", ""),
                "refresh_finished_at": meta2.get("refresh_finished_at", ""),
                "refresh_duration_ms": meta2.get("refresh_duration_ms", 0),
                "last_successful_snapshot_at": meta2.get("last_successful_snapshot_at", ""),
                "next_scheduled_at": meta2.get("next_scheduled_at", ""),
                "skipped_due_to_lock": meta2.get("skipped_due_to_lock", 0),
                "consecutive_failures": meta2.get("consecutive_failures", 0),
                "actual_snapshot_interval_seconds": meta2.get("actual_snapshot_interval_seconds", 0),
            },
            "market_overview": overview,
            "candidates": {
                "total": len(cands),
                "researched": len(research),
                "not_researched": not_researched,      # 尚未研究
                "complete": len(ok),
                "qualified": len(qualified),            # 正式评分正向（候选级）
                "veto_count": len(vetoed),              # 候选级VETO
                "formal_decision_count": 0,             # 正式决策数（INTRADAY不产出，需DECISION）
                "degraded": len(degraded),
                "pending": len(pending),
            },
            "research_results": research,
            "note": "盘中强势候选为候选级结论；正式建仓/加减仓等交易动作需进入DECISION层完成数据检查、完整VETO、持仓与仓位计算后输出。",
            "performance": {
                "stage1_seconds": round(t1 - t0, 2),
                "stage2_seconds": round(t2 - t1, 2),
                "total_seconds": round(t2 - t0, 2),
                "network_calls": 0,   # 查询阶段网络0
                "model_calls": 0,     # 查询阶段模型0
            },
        }

        if verbose:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return result

    def _latest_completed_trade_date(self) -> str:
        try:
            d = TradingCalendarService().latest_trade_date(as_str=True)
            return d or ""
        except Exception:
            return ""


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="今日查询两阶段流程")
    parser.add_argument("--query", default="今天有什么好票")
    parser.add_argument("--max-research", type=int, default=15)
    parser.add_argument("--max-candidates", type=int, default=50)
    args = parser.parse_args()

    # 意图路由
    horizon = route_query(args.query)
    print(f"查询: {args.query} | 意图: {horizon.value}")

    if horizon == AnalysisHorizon.INTRADAY_SCAN:
        pipeline = IntradayPipeline()
        pipeline.run(max_research=args.max_research)
    elif horizon == AnalysisHorizon.EOD_SCAN:
        print("收盘后扫描：使用最近已完成交易日数据（走 Scanner EOD 链路）")
    else:
        print(f"{horizon.value}：个股研究/决策走独立链路")


if __name__ == "__main__":
    main()
