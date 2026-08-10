from __future__ import annotations

import time
from datetime import datetime

from database.db import get_connection
from scripts.analyze_stock import (
    fundamental_factor,
    load_announcements,
    load_bars,
    load_financials,
    load_news,
    policy_factor,
    sentiment_factor,
    sentiment_factor_ai,
    technical_factor,
)
from scripts.stock_report import TZ, StageTimings, _check_freshness, fetch_and_persist
from data_hub.services.capital_flow_enhanced import capital_flow_factor_enhanced
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
from trading.decision_support.task_context import TaskContext


TIMING_STAGES = [
    "行情",
    "K线",
    "财务",
    "公告",
    "新闻",
    "情绪AI",
    "数据库",
    "五维计算",
    "DecisionEngine",
    "总耗时",
]


class _NoPositionReader:
    """Stock-report CLI has no authenticated portfolio identity; remain read-only."""

    def get_holding(self, local_user_id: str, symbol: str):
        return None


class StockReportService:
    def _load_inputs(self, symbol: str):
        with get_connection(read_only=True) as connection:
            bars = load_bars(connection, symbol)
            financials = load_financials(connection, symbol)
            news = load_news(connection, symbol)
            announcements = load_announcements(connection, symbol)
        return bars, financials, news, announcements

    def run(
        self,
        *,
        symbol: str,
        days: int = 90,
        no_fetch: bool = False,
        no_ai: bool = False,
        stale_ok: bool = False,
    ) -> str:
        total_started = time.perf_counter()
        timings = StageTimings()
        code = symbol.strip()

        if not no_fetch:
            fetch_and_persist(code, days, timings=timings)

        with timings.measure("数据库"):
            bars, financials, news, announcements = self._load_inputs(code)
        if not bars:
            raise ValueError(f"数据库无 {code} 行情数据")

        stale = not stale_ok and not _check_freshness(code, bars)
        # The acquisition pipeline is executed at most once per request. A stale
        # result is reported explicitly instead of repeating the whole analysis.

        with timings.measure("五维计算"):
            technical = technical_factor(bars)
            fundamental = fundamental_factor(financials)
            capital = capital_flow_factor_enhanced(code, bars)
            if no_ai:
                sentiment = sentiment_factor(news, datetime.now(tz=TZ))
            else:
                with timings.measure("情绪AI"):
                    sentiment = sentiment_factor_ai(code, news, datetime.now(tz=TZ))
            policy = policy_factor(announcements)
            combined = technical["score"] * 0.6 + fundamental["score"] * 0.4
            confidence = (
                technical["confidence"] * 0.6
                + fundamental["confidence"] * 0.4
            )

        data_status = DataStatus.STALE if stale else DataStatus.FRESH
        decision_input = DecisionInput(
            task=TaskContext(
                local_user_id="stock-report",
                external_user_id="stock-report-cli",
                channel="api",
                mode="RESEARCH",
                symbol=code,
                snapshot_id=f"stock-report-{code}-{bars[-1].trade_date}",
                trade_date=str(bars[-1].trade_date),
                data_cutoff=datetime.now(tz=TZ),
            ),
            formal_score=combined,
            technical_score=technical["score"],
            fundamental_score=fundamental["score"],
            sentiment_score=sentiment["score"],
            policy_score=policy["score"],
            capital_score=capital["score"],
            confidence=confidence,
            data_status=data_status,
            current_price=bars[-1].close,
            bars=bars,
            coverage_ratio=1.0,
        )
        with timings.measure("DecisionEngine"):
            decision = DecisionEngine(
                manual_positions=_NoPositionReader(),
            ).decide(decision_input)

        timings.record("总耗时", time.perf_counter() - total_started)
        lines = [
            "=" * 60,
            f"  {code} 五维研究报告 | {datetime.now(tz=TZ).date()}",
            "=" * 60,
            (
                f"  行情: K线 {len(bars)} 根 | 最新收盘 "
                f"{bars[-1].close:.2f} ({bars[-1].trade_date})"
            ),
        ]
        if stale:
            lines.append("  数据状态: STALE（未重复执行完整分析流程）")
        lines.extend(
            [
                f"  技术面 {technical['score']:+.4f} | {technical['summary'][:50]}",
                f"  基本面 {fundamental['score']:+.4f} | {fundamental['summary'][:50]}",
                (
                    f"  资金面 {capital['score']:+.4f} | "
                    f"{capital.get('pv_label', '')} | {capital.get('label', '')}"
                ),
                f"  情绪面 {sentiment['score']:+.4f} | 事件{len(sentiment['events'])}条",
                (
                    f"  政策面 {policy['score']:+.4f} | 公告"
                    f"{policy.get('total_announcements', len(policy['notes']))}条"
                ),
                f"  60/40综合分 {combined:+.4f} | 置信度 {confidence:.3f}",
                f"  正式动作(DecisionEngine): {decision.action_zh}",
                f"  执行状态: {decision.execution_status}",
                f"  下一步: {decision.next_action}",
                "-" * 60,
                "阶段耗时:",
                *timings.render(TIMING_STAGES),
                "-" * 60,
                "⚠️ 以上为系统规则计算结果，仅供研究参考，不构成投资建议；"
                "股市有风险，决策需自主。",
                "=" * 60,
            ]
        )
        return "\n".join(lines)


__all__ = ["StockReportService", "TIMING_STAGES"]
