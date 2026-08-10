"""市场交易时段解析器 MarketSessionResolver（2026-08 任务二）

区分5种市场阶段：
- PRE_MARKET：开盘前
- INTRADAY：交易时段
- POST_MARKET_PENDING：收盘后但当日日线未完成
- POST_MARKET_READY：当日日线已完成
- NON_TRADING_DAY：周末或节假日

路由规则：
1. INTRADAY："今天有什么好票" → INTRADAY_SCAN（实时快照）
2. PRE_MARKET：用上一完成交易日日线+隔夜公告/新闻（不得把昨日快照冒充今日盘中）
3. POST_MARKET_PENDING：用最终盘中快照，明确日线尚未完成
4. POST_MARKET_READY：优先 EOD_SCAN（当日完整收盘）
5. NON_TRADING_DAY：用最近完成交易日 EOD_SCAN

输出必须标记当前市场阶段。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from enum import StrEnum
from typing import Optional

TZ = timezone(timedelta(hours=8))

# A股交易时段（本地时间）
PRE_MARKET_END = time(9, 30)      # 开盘
LUNCH_BREAK_START = time(11, 30)  # 午休
LUNCH_BREAK_END = time(13, 0)     # 下午开盘
CLOSE_TIME = time(15, 0)          # 收盘


class MarketSession(StrEnum):
    PRE_MARKET = "PRE_MARKET"                    # 开盘前
    INTRADAY = "INTRADAY"                        # 交易时段
    POST_MARKET_PENDING = "POST_MARKET_PENDING"  # 收盘后日线未完成
    POST_MARKET_READY = "POST_MARKET_READY"      # 收盘后日线已完成
    NON_TRADING_DAY = "NON_TRADING_DAY"          # 周末/节假日


SESSION_ZH = {
    MarketSession.PRE_MARKET: "开盘前",
    MarketSession.INTRADAY: "交易时段",
    MarketSession.POST_MARKET_PENDING: "收盘后（日线待完成）",
    MarketSession.POST_MARKET_READY: "收盘后（日线已完成）",
    MarketSession.NON_TRADING_DAY: "非交易日",
}


@dataclass
class SessionResolution:
    session: MarketSession
    session_zh: str
    is_trading_day: bool
    latest_completed_trade_date: str = ""   # 最近完成交易日
    daily_data_available: bool = False       # 当日日线是否已完成
    now_str: str = ""

    def render(self) -> str:
        return f"市场阶段: {self.session_zh}"


class MarketSessionResolver:
    """市场阶段解析（交易日历 + 本地时间）"""

    def __init__(self, calendar_service=None):
        self.calendar = calendar_service

    def _get_calendar(self):
        if self.calendar is None:
            from data_hub.services.trading_calendar_service import TradingCalendarService
            self.calendar = TradingCalendarService()
        return self.calendar

    def resolve(
        self, now: Optional[datetime] = None,
        daily_data_available: Optional[bool] = None,
    ) -> SessionResolution:
        """解析当前市场阶段

        :param now: 当前时间（测试可注入）；默认系统时间
        :param daily_data_available: 当日日线是否已入库（None=自动判断）
        """
        now = now or datetime.now(tz=TZ)
        today = now.date()
        cal = self._get_calendar()

        # 是否交易日
        is_open_today = False
        try:
            is_open_today = cal.is_open_day(today)
        except Exception:
            pass

        if not is_open_today:
            # 周末/节假日
            latest = self._latest_completed(now)
            return SessionResolution(
                session=MarketSession.NON_TRADING_DAY,
                session_zh=SESSION_ZH[MarketSession.NON_TRADING_DAY],
                is_trading_day=False,
                latest_completed_trade_date=latest,
                now_str=now.strftime("%Y-%m-%d %H:%M"),
            )

        # 交易日：按时段判断
        t = now.time()
        if t < PRE_MARKET_END:
            session = MarketSession.PRE_MARKET
        elif PRE_MARKET_END <= t < CLOSE_TIME and not (LUNCH_BREAK_START <= t < LUNCH_BREAK_END):
            session = MarketSession.INTRADAY
        elif CLOSE_TIME <= t:
            # 收盘后：当日日线是否完成？
            if daily_data_available is None:
                # 自动判断：默认收盘后日线需要数据源确认；简化用时间启发式
                # （真实确认应查数据源最新 trade_date == 今天）
                daily_data_available = self._is_daily_ready(now)
            session = (
                MarketSession.POST_MARKET_READY if daily_data_available
                else MarketSession.POST_MARKET_PENDING
            )
        else:
            # 午休时段（11:30-13:00）视为盘中（行情暂停）
            session = MarketSession.INTRADAY

        latest = self._latest_completed(now)
        return SessionResolution(
            session=session,
            session_zh=SESSION_ZH[session],
            is_trading_day=True,
            latest_completed_trade_date=latest,
            daily_data_available=(session == MarketSession.POST_MARKET_READY),
            now_str=now.strftime("%Y-%m-%d %H:%M"),
        )

    def _latest_completed(self, now: datetime) -> str:
        try:
            d = self._get_calendar().latest_trade_date(data_cutoff=now, as_str=True)
            return d or ""
        except Exception:
            return ""

    def _is_daily_ready(self, now: datetime) -> bool:
        """当日日线是否已完成（收盘后数据源确认）。

        简化实现：收盘后1小时（16:00后）默认数据源已更新；
        精确确认应查数据源最新 trade_date == 今天。
        """
        if now.time() >= time(16, 0):
            return True
        # 16:00前：需要数据源确认（此处保守返回 False）
        return False

    def route_for(self, query: str, now: Optional[datetime] = None) -> tuple[MarketSession, str]:
        """结合意图路由与市场阶段，返回 (session, 路由建议)"""
        from trading.scanner.query_router import route_query
        from trading.scanner.query_router import AnalysisHorizon

        horizon = route_query(query)
        session = self.resolve(now).session

        # 盘中查询 + 交易时段 → INTRADAY_SCAN
        if horizon == AnalysisHorizon.INTRADAY_SCAN and session == MarketSession.INTRADAY:
            return session, "INTRADAY_SCAN"
        # 盘中查询 + 非交易时段 → 降级到最近完成日线
        if horizon == AnalysisHorizon.INTRADAY_SCAN:
            if session in (MarketSession.PRE_MARKET, MarketSession.NON_TRADING_DAY):
                return session, "EOD_SCAN（最近完成日线，非今日盘中）"
            if session == MarketSession.POST_MARKET_PENDING:
                return session, "INTRADAY_SCAN（最终盘中快照，日线未完成）"
            if session == MarketSession.POST_MARKET_READY:
                return session, "EOD_SCAN（当日收盘数据）"
        return session, horizon.value
