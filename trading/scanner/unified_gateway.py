"""统一扫描网关（三端共用：Web / 企业微信 / QQ）

统一链路：
QueryRouter → IntradayScanner/EodScanner → ResearchService → DecisionService

禁止任何入口：
- 临时执行 Shell
- 直接调用旧 scanner_cli 绕过 QueryRouter
- 自行拼接 Tushare 查询
- 使用不同动作阈值
- 绕过 VETO 或新鲜度守卫

三端用同一请求条件/用户/快照时，核心结果必须一致（仅展示格式不同）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from trading.scanner.query_router import AnalysisHorizon, route_query

TZ = timezone(timedelta(hours=8))


@dataclass
class ScanRequest:
    query: str
    local_user_id: str = "u_wecom_ZhangTianYi"
    external_user_id: str = "ZhangTianYi"
    channel: str = "api"           # web / wecom / qq / api
    max_research: int = 15
    max_candidates: int = 50
    force_realtime: bool = False


@dataclass
class ScanResponse:
    query: str
    query_intent: str = ""
    realtime_snapshot_id: str = ""
    daily_snapshot_id: str = ""
    formal_score: float = 0.0
    veto_result: str = "NO_VETO"
    action: str = ""
    data_cutoff: str = ""
    candidates: list = field(default_factory=list)
    research_results: list = field(default_factory=list)
    elapsed_seconds: float = 0.0
    channel: str = "api"
    market_session: str = ""        # 市场阶段（PRE_MARKET/INTRADAY/...）
    market_session_zh: str = ""
    latest_completed_trade_date: str = ""
    errors: list = field(default_factory=list)


class UnifiedScanGateway:
    """统一网关：所有入口走此链路（单例）"""

    _instance: Optional["UnifiedScanGateway"] = None

    def __new__(cls) -> "UnifiedScanGateway":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        self._pipeline = None

    def _get_pipeline(self):
        if self._pipeline is None:
            from scripts.intraday_pipeline import IntradayPipeline
            self._pipeline = IntradayPipeline()
        return self._pipeline

    # ------------------------------------------------------------------
    # 统一入口
    # ------------------------------------------------------------------
    def scan(self, req: ScanRequest) -> ScanResponse:
        t0 = time.perf_counter()
        resp = ScanResponse(query=req.query, channel=req.channel)

        # 0. 市场阶段解析（交易时段感知，先于关键词路由）
        from trading.scanner.market_session import MarketSessionResolver
        session_resolver = MarketSessionResolver()
        session = session_resolver.resolve()
        resp.market_session = session.session.value
        resp.market_session_zh = session.session_zh
        resp.latest_completed_trade_date = session.latest_completed_trade_date

        # 1. 意图路由 + 市场阶段联合判定（统一）
        from trading.scanner.query_router import AnalysisHorizon, route_query
        horizon = route_query(req.query)

        # 盘中查询但非交易时段 → 按市场阶段修正
        if horizon == AnalysisHorizon.INTRADAY_SCAN and session.session.value != "INTRADAY":
            if session.session.value in ("PRE_MARKET", "NON_TRADING_DAY"):
                horizon = AnalysisHorizon.EOD_SCAN   # 用最近完成日线
                resp.query_intent = f"{horizon.value}({session.session_zh})"
            elif session.session.value == "POST_MARKET_PENDING":
                resp.query_intent = f"INTRADAY_SCAN({session.session_zh})"
            elif session.session.value == "POST_MARKET_READY":
                horizon = AnalysisHorizon.EOD_SCAN   # 当日收盘数据
                resp.query_intent = f"{horizon.value}({session.session_zh})"
        else:
            resp.query_intent = horizon.value

        # 2. 按意图分流（统一服务，无Shell、无scanner_cli绕过）
        if horizon == AnalysisHorizon.INTRADAY_SCAN:
            result = self._intraday(req, resp)
        elif horizon == AnalysisHorizon.EOD_SCAN:
            result = self._eod(req, resp)
        else:
            # RESEARCH/DECISION → 走研究/决策服务（此处返回待分流提示）
            resp.action = "需要个股研究/决策服务"
            resp.errors.append(f"{horizon.value} 走研究/决策链路")
            result = resp

        result.elapsed_seconds = round(time.perf_counter() - t0, 3)
        return result

    # ------------------------------------------------------------------
    # INTRADAY_SCAN：两阶段 + 候选级动作
    # ------------------------------------------------------------------
    def _intraday(self, req: ScanRequest, resp: ScanResponse) -> ScanResponse:
        pipeline = self._get_pipeline()
        result = pipeline.run(max_research=req.max_research, verbose=False)

        # 双时间轴快照ID
        resp.realtime_snapshot_id = result["data_time"].get("realtime_status", "")
        rt_snap = result["data_time"].get("last_successful_snapshot_at", "")
        resp.data_cutoff = result["data_time"].get("realtime", "")
        resp.candidates = result["candidates"]
        resp.research_results = result["research_results"]

        # 候选级动作（非正式交易动作）
        candidates = result["research_results"]
        top = next((c for c in candidates if c.get("status") == "OK"), None)
        if top:
            from scripts.intraday_pipeline import IntradayPipeline
            action = IntradayPipeline.candidate_action_for(top)
            resp.action = action["action"]
            resp.formal_score = top.get("formal_score", 0)
            resp.veto_result = top.get("veto", "NO_VETO")
        else:
            resp.action = "暂不参与（无有效研究结果）"
        return resp

    # ------------------------------------------------------------------
    # EOD_SCAN：静态快照扫描（统一 MarketScannerService）
    # ------------------------------------------------------------------
    def _eod(self, req: ScanRequest, resp: ScanResponse) -> ScanResponse:
        try:
            from trading.scanner.service import MarketScannerService
            service = MarketScannerService()
            result = service.run_scan(req.query, top_n=min(req.max_candidates, 20))
            resp.daily_snapshot_id = getattr(result, "snapshot_id", "") or ""
            resp.candidates = getattr(result, "matched_count", 0)
            resp.action = "EOD扫描完成（静态快照）"
            resp.research_results = []
        except Exception as exc:
            resp.errors.append(f"EOD扫描失败: {exc}")
            resp.action = "暂不参与（EOD扫描异常）"
        return resp
