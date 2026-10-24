from __future__ import annotations

import json
import os
import time
import uuid
from datetime import date, datetime

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
from data_hub.services.name_change_service import (
    NameChangeService,
    render_name_change_history,
)
from trading.decision_support.action_resolver import VetoResult
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
from trading.decision_support.formal_score_resolver import FormalScoreResolver
from trading.decision_support.portfolio_context import PortfolioContext
from trading.decision_support.task_context import TaskContext
from trading.research.orchestration.cross_section import FACTOR_WEIGHTS
from trading.research.stock_report_performance import StockReportPerformance
from trading.research.sentiment.black_swan_rules import match_black_swan


TOP_LIST_VETO_THRESHOLD = -1e8  # 龙虎榜区间净卖超1亿 → 硬否决


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


_formal_score_resolver = FormalScoreResolver()


class _NoPositionReader:
    """Stock-report CLI has no authenticated portfolio identity; remain read-only."""

    def get_holding(self, local_user_id: str, symbol: str):
        return None


class StockReportService:
    def _load_inputs(
        self,
        symbol: str,
        performance: StockReportPerformance,
        report_cutoff: datetime,
    ):
        connect_started = time.perf_counter()
        connection = get_connection(read_only=True)
        performance.record(
            "db_connect_ms",
            (time.perf_counter() - connect_started) * 1_000.0,
        )
        query_started = time.perf_counter()
        with connection:
            with performance.measure("bars_ms"):
                bars = load_bars(connection, symbol)
            with performance.measure("financial_ms"):
                financials = load_financials(connection, symbol)
            with performance.measure("news_ms"):
                news = load_news(connection, symbol)
            with performance.measure("announcement_ms"):
                announcements = load_announcements(
                    connection,
                    symbol,
                    days=getattr(self, "_announcement_days", 60),
                )
            latest_trade_row = connection.execute(
                """
                SELECT MAX(calendar_date) FILTER (
                    WHERE is_trading_day = TRUE
                      AND (
                        calendar_date < CAST(? AS DATE)
                        OR (
                            calendar_date = CAST(? AS DATE)
                            AND EXTRACT(hour FROM ?) >= 16
                        )
                      )
                ), MAX(calendar_date)
                FROM trading_calendar_days
                WHERE data_cutoff <= ?
                """,
                [report_cutoff, report_cutoff, report_cutoff, report_cutoff],
            ).fetchone()
            snapshot_row = connection.execute(
                """
                SELECT i.payload_json
                FROM market_snapshot_items i
                JOIN market_snapshot_runs r USING(snapshot_id)
                WHERE i.symbol LIKE ?
                ORDER BY r.snapshot_time DESC
                LIMIT 1
                """,
                [f"%{symbol}%"],
            ).fetchone()
            snapshot_payload = (
                json.loads(snapshot_row[0])
                if snapshot_row is not None and snapshot_row[0]
                else {}
            )
            self._snapshot_moneyflow_net = snapshot_payload.get("moneyflow_net")
        performance.record(
            "db_query_ms",
            (time.perf_counter() - query_started) * 1_000.0,
        )
        calendar_covers_cutoff = (
            latest_trade_row is not None
            and latest_trade_row[1] is not None
            and latest_trade_row[1] >= report_cutoff.date()
        )
        latest_trade_date = (
            latest_trade_row[0] if calendar_covers_cutoff else None
        )
        return bars, financials, news, announcements, latest_trade_date

    @staticmethod
    def _local_freshness(bars: list, latest_trade_date: date | None) -> bool:
        if not bars:
            return False
        if latest_trade_date is None:
            # Local calendar coverage is not current enough to prove freshness.
            # Stay conservative and never fall back to a network call.
            return False
        return bars[-1].trade_date >= latest_trade_date

    def run(
        self,
        *,
        symbol: str,
        days: int = 90,
        no_fetch: bool = False,
        no_ai: bool = False,
        stale_ok: bool = False,
        request_id: str | None = None,
    ) -> str:
        total_started = time.perf_counter()
        timings = StageTimings()
        code = symbol.strip()
        report_cutoff = datetime.now(tz=TZ)
        performance = StockReportPerformance(
            request_id=request_id or f"stock-report-{uuid.uuid4().hex}",
            symbol=code,
            mode="RESEARCH",
            no_fetch=no_fetch,
            database_owner_pid=os.getpid(),
        )
        try:
            if not no_fetch:
                fetch_and_persist(code, days, timings=timings)

            self._announcement_days = max(days, 60)
            with timings.measure("数据库"):
                (
                    bars,
                    financials,
                    news,
                    announcements,
                    local_latest_trade_date,
                ) = self._load_inputs(code, performance, report_cutoff)
            if not bars:
                raise ValueError(f"数据库无 {code} 行情数据")

            name_history = NameChangeService().get_history(
                code,
                allow_external_fetch=not no_fetch,
            )
            identity_line = render_name_change_history(
                name_history,
                as_of=report_cutoff.date(),
            )

            if no_fetch:
                fresh = self._local_freshness(bars, local_latest_trade_date)
            else:
                fresh = _check_freshness(code, bars)
            stale = not stale_ok and not fresh

            with timings.measure("五维计算"):
                with performance.measure("technical_ms"):
                    technical = technical_factor(bars)
                with performance.measure("fundamental_ms"):
                    fundamental = fundamental_factor(financials)
                with performance.measure("capital_ms"):
                    capital = capital_flow_factor_enhanced(
                        code,
                        bars,
                        allow_external_fetch=not no_fetch,
                    )
                with performance.measure("sentiment_ms"):
                    # 仅黑天鹅公告进入情绪事件流并置顶；普通公告仍只进政策面，
                    # 防止中性公告占满模型条数上限、挤掉原有新闻。
                    forced_announcements = [
                        item for item in announcements
                        if match_black_swan(
                            f"{item.get('title', '')} {item.get('content', '')}",
                            source_level=item.get("source_level", "media"),
                            source_name=item.get("publisher", ""),
                            verified=bool(item.get("verified", False)),
                        ) is not None
                    ]
                    sentiment_items = [*forced_announcements, *news]
                    if no_ai:
                        sentiment = sentiment_factor(sentiment_items, report_cutoff)
                        # 方案B（2026-09-09 用户选型）：默认关键词快跑，
                        # 但关键词情绪分<=-0.1（真·负面新闻流）时自动用 qwen 复核，
                        # 干净/正面票保持快速。仅按分数触发——按"负面词命中数"太松
                        # （"下降/承压/风险"等常见词几乎每只票都有），会退化成全量AI。
                        if sentiment["score"] <= -0.1:
                            with timings.measure("情绪AI"):
                                try:
                                    ai_sentiment = sentiment_factor_ai(
                                        code, sentiment_items, report_cutoff
                                    )
                                    if ai_sentiment and ai_sentiment.get("events"):
                                        sentiment = ai_sentiment
                                except Exception:
                                    pass  # AI 失败保留关键词结果，不阻塞分析
                    else:
                        with timings.measure("情绪AI"):
                            sentiment = sentiment_factor_ai(
                                code,
                                sentiment_items,
                                report_cutoff,
                            )
                with performance.measure("policy_ms"):
                    policy = policy_factor(announcements)
                combined = technical["score"] * 0.6 + fundamental["score"] * 0.4
                # 四维横截面 composite（技术30/基本30/情绪25/资金15 rank加权）优先；
                # 读不到时保留 60/40（报告为分析展示，不走 G 守卫）。
                cross = _formal_score_resolver.read_cross_section(code, with_detail=True)
                score_source = "60/40"
                cross_detail = None
                if cross is not None:
                    combined, cross_detail = cross
                    score_source = "四维"
                confidence = (
                    technical["confidence"] * 0.6
                    + fundamental["confidence"] * 0.4
                )
                # 龙虎榜硬否决：区间净卖超阈值 → 覆盖一切正向评分
                top_info = capital.get("top_list") or {}
                top_net = top_info.get("total_net")
                top_veto = None
                if top_net is not None and top_net <= TOP_LIST_VETO_THRESHOLD:
                    top_veto = VetoResult(
                        veto_triggered=True,
                        veto_type="TOP_LIST_NET_SELL",
                        veto_reason=(
                            f"龙虎榜区间({top_info.get('first_date', '')}~"
                            f"{top_info.get('last_date', '')})净卖"
                            f"{top_net / 1e8:.2f}亿，机构/游资派发"
                        ),
                        evidence_source="Tushare top_list",
                        evidence_time=str(bars[-1].trade_date),
                        confidence=0.8,
                    )
                # 基本面质量硬否决：最新报告期亏损 或 归母净利同比大降(<-30%) → 覆盖正向评分
                # （2026-09-15 教训：开滦股份亏损/苏垦农发-32%/华兰生物-25% 均被板块情绪拉高判建仓）
                fund_veto = None
                f_net_income = fundamental.get("net_income")
                f_np_yoy = fundamental.get("net_profit_yoy")
                if f_net_income is not None and f_net_income < 0:
                    fund_veto = VetoResult(
                        veto_triggered=True,
                        veto_type="FUNDAMENTAL_LOSS",
                        veto_reason=(
                            f"最新报告期归母净利亏损"
                            f"({f_net_income / 1e8:.2f}亿)，基本面实质恶化"
                        ),
                        evidence_source="Tushare财报",
                        evidence_time=str(bars[-1].trade_date),
                        confidence=0.8,
                    )
                elif f_np_yoy is not None and f_np_yoy < -0.30:
                    fund_veto = VetoResult(
                        veto_triggered=True,
                        veto_type="FUNDAMENTAL_NET_PROFIT_DOWN",
                        veto_reason=(
                            f"归母净利同比{f_np_yoy * 100:.1f}%"
                            f"(<-30%)，基本面恶化"
                        ),
                        evidence_source="Tushare财报",
                        evidence_time=str(bars[-1].trade_date),
                        confidence=0.8,
                    )
                # 多 veto 取最严重（基本面亏损 > 净利大降 > 龙虎榜派发）
                veto = fund_veto or top_veto or None

            data_status = DataStatus.STALE if stale else DataStatus.FRESH
            point_in_time_ok = all(
                bar.trade_date <= report_cutoff.date() for bar in bars
            )
            decision_input = DecisionInput(
                task=TaskContext(
                    local_user_id="stock-report",
                    external_user_id="stock-report-cli",
                    channel="api",
                    mode="RESEARCH",
                    symbol=code,
                    snapshot_id=f"stock-report-{code}-{bars[-1].trade_date}",
                    trade_date=str(bars[-1].trade_date),
                    data_cutoff=report_cutoff,
                ),
                formal_score=combined,
                technical_score=technical["score"],
                fundamental_score=fundamental["score"],
                point_in_time_ok=point_in_time_ok,
                account_context=PortfolioContext.research_default(
                    local_user_id="stock-report",
                    as_of=report_cutoff,
                ),
                sentiment_score=sentiment["score"],
                policy_score=policy["score"],
                capital_score=capital["score"],
                confidence=confidence,
                data_status=data_status,
                veto=veto,
                current_price=bars[-1].close,
                bars=bars,
                coverage_ratio=1.0,
            )
            with timings.measure("DecisionEngine"):
                with performance.measure("decision_ms"):
                    decision = DecisionEngine(
                        manual_positions=_NoPositionReader(),
                    ).decide(decision_input)

            timings.record("总耗时", time.perf_counter() - total_started)
            with performance.measure("render_ms"):
                last_bar = bars[-1]
                price_kind = (
                    "盘中最新价，未收盘"
                    if last_bar.trade_date == report_cutoff.date()
                    and report_cutoff.hour < 15
                    else "收盘价"
                )

                def _yi(v: float | None) -> str:
                    return "N/A" if v is None else f"{v / 1e8:+.2f}亿"

                def _pct(v: float | None) -> str:
                    return "N/A" if v is None else f"{v * 100:.1f}%"

                yoy = fundamental.get("net_profit_yoy")
                yoy_txt = "N/A(缺上年同期)" if yoy is None else f"{yoy * 100:+.1f}%"
                revenue_yoy = fundamental.get("revenue_yoy")
                revenue_yoy_txt = (
                    "N/A" if revenue_yoy is None else f"{revenue_yoy * 100:+.1f}%"
                )
                roe_source = fundamental.get("roe_source") or "口径未知"
                report_period = fundamental.get("report_period")
                deducted = fundamental.get("net_profit_deducted")
                fund_detail = (
                    f"负债率{_pct(fundamental.get('debt_ratio'))} | "
                    f"ROE{_pct(fundamental.get('roe'))}（{roe_source}） | "
                    f"扣非ROE{_pct(fundamental.get('roe_dt'))} | "
                    f"营收同比{revenue_yoy_txt} | "
                    f"经营现金流{_yi(fundamental.get('cashflow'))} | "
                    f"净利同比{yoy_txt}"
                    + (
                        f" | 扣非净利{_yi(deducted)}"
                        + (
                            f"(投资收益{_yi(fundamental.get('invest_income'))})"
                            if fundamental.get("invest_income") is not None
                            else ""
                        )
                        if deducted is not None
                        else ""
                    )
                    + (f" | 报告期{report_period}" if report_period else "")
                )

                mf = capital.get("moneyflow") or {}
                mf5_ratio = capital.get("mf5_ratio")
                turnover = capital.get("turnover")
                if turnover:
                    td = str(turnover.get("trade_date", ""))
                    turn_txt = (
                        f"换手率{turnover['turnover_rate']:.2f}%"
                        f"({td[4:6]}-{td[6:8]}收盘)"
                    )
                    turn_pct = turnover.get("turnover_percentile_20d")
                    if turn_pct is not None:
                        turn_txt += f" | 近20日分位{turn_pct:.0%}"
                else:
                    turn_txt = "换手率N/A"
                top_net_txt = _yi(top_net)
                if top_net is not None:
                    fd = str(top_info.get("first_date", ""))
                    ld = str(top_info.get("last_date", ""))
                    top_net_txt += f"({fd[4:6]}-{fd[6:8]}~{ld[4:6]}-{ld[6:8]})"
                margin_label = (capital.get("margin") or {}).get("label", "")
                technical_evidence = [
                    item
                    for item in technical.get("evidence", [])
                    if not str(item).startswith("bar:")
                ]
                technical_evidence_txt = "；".join(technical_evidence)

                lines = [
                    "=" * 60,
                    f"  {code} 五维研究报告 | {report_cutoff.date()}",
                    "=" * 60,
                    (
                        f"  行情: K线 {len(bars)} 根 | 现价 "
                        f"{last_bar.close:.2f}（{last_bar.trade_date} {price_kind}）"
                    ),
                    (
                        f"  时点: 评分基准={last_bar.trade_date}收盘 | "
                        f"报告生成={report_cutoff:%Y-%m-%d %H:%M}"
                    ),
                ]
                if stale:
                    lines.append("  数据状态: STALE（未重复执行完整分析流程）")
                if identity_line:
                    lines.insert(5, f"  {identity_line}")
                if top_net is not None and top_net <= TOP_LIST_VETO_THRESHOLD:
                    lines.append(
                        f"  ⚠️ 龙虎榜硬否决：区间净卖 {abs(top_net) / 1e8:.2f}亿，"
                        "机构/游资派发（覆盖一切正向评分）"
                    )
                if fund_veto is not None:
                    lines.append(
                        f"  ⚠️ 基本面硬否决：{fund_veto.veto_reason}"
                        "（覆盖一切正向评分）"
                    )
                mf20 = mf.get("net20")
                if top_net is not None and top_net < 0 and mf20 and mf20 > 0:
                    lines.append(
                        f"  ⚠️ 资金信号矛盾：龙虎榜净卖{top_net / 1e8:.2f}亿"
                        f"（席位口径，仅上榜日）与主力20日净流入"
                        f"{mf20 / 1e8:.2f}亿（全市场大单估算）并存；"
                        "口径与时窗不同，席位显性派发按硬否决优先"
                    )
                lines.extend(
                    [
                        (
                            f"  技术面 {technical['score']:+.4f} = 趋势×0.5+动量×0.35"
                            f"+RSI×0.15 | {technical['summary'][:150]}"
                            + (
                                f" | {technical_evidence_txt}"
                                if technical_evidence_txt
                                else ""
                            )
                        ),
                        (
                            f"  基本面 {fundamental['score']:+.4f} = "
                            f"可用分项均值(ROE/负债率/现金流) | {fund_detail}"
                        ),
                        (
                            f"  资金面 {capital['score']:+.4f} = 量价40%+两融40%"
                            f"+龙虎榜20%（缺项权重摊给有数据项） | "
                            f"{capital.get('pv_label', '')}"
                        ),
                        (
                            f"     主力净额: 20日{_yi(mf.get('net20'))} / "
                            f"10日{_yi(mf.get('net10'))} / 5日{_yi(mf.get('net5'))}"
                            + (
                                f"(占流通市值{abs(mf5_ratio):.1%})"
                                if mf5_ratio is not None
                                else ""
                            )
                            + (
                                f"（5日含{str(mf.get('last_date', ''))[4:6]}-"
                                f"{str(mf.get('last_date', ''))[6:8]}单日"
                                f"{_yi(mf.get('net1'))}）"
                                if mf.get("net1") is not None
                                and mf.get("last_date")
                                else ""
                            )
                        ),
                        f"     {turn_txt} | 龙虎榜净额{top_net_txt} | 两融: {margin_label}",
                        f"  情绪面 {sentiment['score']:+.4f} = qwen方向×强度×新鲜度/√n | 事件{len(sentiment['events'])}条",
                        (
                            f"  政策面 {policy['score']:+.4f} = 公告均值；强制利空按最负事件×80%保底 | 公告"
                            f"{policy.get('total_announcements', len(policy['notes']))}条"
                        ),
                        (
                            "     （公告日期=巨潮+东财双源交叉（取更早披露时间）；"
                            "无法交叉时以巨潮口径为准）"
                        ),
                        f"  {score_source}综合分 {combined:+.4f} | 置信度 {confidence:.3f}",
                        f"  正式动作(DecisionEngine): {decision.action_zh}",
                        f"  执行状态: {decision.execution_status}",
                        f"  仓位: 当前{decision.current_position_ratio:.1%} → 目标{decision.target_position_ratio:.1%}（分{decision.recommended_batches}批）",
                        *(
                            [
                                f"  建仓区间: {decision.frozen_entry_zone[0]:.2f}-{decision.frozen_entry_zone[1]:.2f}元"
                                if decision.frozen_entry_zone
                                else "  建仓区间: N/A"
                            ]
                        ),
                        *(
                            [
                                f"  优选区间: {decision.frozen_preferred_zone[0]:.2f}-{decision.frozen_preferred_zone[1]:.2f}元"
                                if decision.frozen_preferred_zone
                                else "  优选区间: N/A"
                            ]
                        ),
                        f"  止损价: {decision.frozen_stop_loss_price:.2f}元"
                        if decision.frozen_stop_loss_price
                        else "  止损价: N/A",
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
                forced_sentiment = [
                    event for event in sentiment.get("events", [])
                    if event.get("force_negative")
                ]
                forced_policy = policy.get("force_negative_events", [])
                force_lines = []
                for event in forced_policy[:5]:
                    force_lines.append(
                        f"  ⚠ {event['marker']} {event.get('date', '')} "
                        f"{event.get('event_type', '')}：{event.get('title', '')}（强制利空）"
                    )
                for event in forced_sentiment[:5]:
                    if not any(event.get("title", "") in line for line in force_lines):
                        force_lines.append(
                            f"  ⚠ {event.get('marker', 'FORCE_NEGATIVE')} "
                            f"{event.get('event_type', '')}：{event.get('title', '')}（强制利空）"
                        )
                if force_lines:
                    insert_at = next(
                        (i for i, line in enumerate(lines) if "综合分" in line),
                        len(lines),
                    )
                    lines[insert_at:insert_at] = force_lines
                # 四维综合分的打分细则（available 归一化后的实际权重）
                if cross_detail is not None and score_source == "四维":
                    ranks = {
                        "tech": cross_detail.get("tech_rank"),
                        "fund": cross_detail.get("fund_rank"),
                        "sent": cross_detail.get("sent_rank"),
                        "margin": cross_detail.get("margin_rank"),
                    }
                    labels = {"tech": "技术", "fund": "基本", "sent": "情绪", "margin": "资金"}
                    missing_reason = {
                        "tech": "K线不足",
                        "fund": "无财务数据入库",
                        "sent": "无快照特征",
                        "margin": "非两融标的/两融数据不足",
                    }
                    valid_w = sum(
                        FACTOR_WEIGHTS[k] for k, v in ranks.items() if v is not None
                    )
                    parts = [
                        f"{labels[k]}rank({v:+.2f})×{FACTOR_WEIGHTS[k] / valid_w * 100:.1f}%"
                        for k, v in ranks.items()
                        if v is not None
                    ]
                    detail_line = "     = " + " + ".join(parts)
                    missing = [
                        f"{labels[k]}缺({missing_reason[k]})"
                        for k, v in ranks.items()
                        if v is None
                    ]
                    if missing:
                        detail_line += (
                            f" | 缺失项权重已摊给有数据因子: {'，'.join(missing)}"
                        )
                    detail_line += (
                        "（情绪rank=SentimentV3：机构净额+龙虎榜+"
                        "换手(反向)+缺口跟随(反向)）"
                    )
                    if (
                        (ranks["fund"] is None or ranks["margin"] is None)
                        and getattr(self, "_snapshot_moneyflow_net", None) is not None
                        and mf5_ratio is not None
                        and mf5_ratio <= -0.05
                    ):
                        detail_line += " | ⚠ 主力资金显著流出（快照口径，未入四维）"
                    insert_idx = next(
                        (i for i, line in enumerate(lines) if "综合分" in line),
                        len(lines),
                    )
                    lines.insert(insert_idx + 1, detail_line)
                # 负面舆情提醒（Qwen 新闻情绪，不进评分，仅作风险提醒）
                negative_events = [
                    e for e in sentiment.get("events", [])
                    if e.get("direction", 0) < 0
                ]
                if sentiment.get("score", 0) <= -0.3 and negative_events:
                    neg_titles = "；".join(
                        e.get("title", "") for e in negative_events[:3]
                    )
                    insert_at = next(
                        (i for i, line in enumerate(lines) if "综合分" in line),
                        len(lines),
                    )
                    lines.insert(
                        insert_at,
                        f"  ⚠️ 负面舆情提醒：情绪面 {sentiment['score']:+.2f}，"
                        f"近期负面事件：{neg_titles}",
                    )
                report = "\n".join(lines)
            return report
        finally:
            performance.finish()


__all__ = ["StockReportService", "TIMING_STAGES"]
