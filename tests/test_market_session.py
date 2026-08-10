"""MarketSessionResolver 交易时段感知路由测试（2026-08 任务二）

验证：
- 5种市场阶段识别（PRE_MARKET/INTRADAY/POST_MARKET_PENDING/POST_MARKET_READY/NON_TRADING_DAY）
- "今天有什么好票"不能固定映射INTRADAY，随市场阶段修正路由
- 输出标记当前市场阶段
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.scanner.market_session import (
    MarketSession, MarketSessionResolver,
)

TZ = timezone(timedelta(hours=8))


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=TZ)


# 2026-08-03 = 周一（交易日）；2026-08-02 = 周日（非交易日）
TRADING_MONDAY = "2026-08-03"
SUNDAY = "2026-08-02"


class _DeterministicCalendar:
    """Keep session tests independent from credentials and live calendars."""

    @staticmethod
    def is_open_day(day: date) -> bool:
        return day.weekday() < 5

    @staticmethod
    def latest_trade_date(
        data_cutoff: datetime | None = None,
        as_str: bool = False,
    ) -> date | str:
        cutoff = data_cutoff or _dt(f"{TRADING_MONDAY} 09:00")
        day = cutoff.date()
        if day.weekday() >= 5 or cutoff.time() < time(15):
            day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day.strftime("%Y%m%d") if as_str else day


@pytest.fixture(autouse=True)
def deterministic_calendar(monkeypatch: pytest.MonkeyPatch):
    calendar = _DeterministicCalendar()
    monkeypatch.setattr(
        MarketSessionResolver,
        "_get_calendar",
        lambda self: calendar,
    )


class TestMarketSessionResolver:
    def test_pre_market(self):
        """开盘前"""
        s = MarketSessionResolver().resolve(now=_dt(f"{TRADING_MONDAY} 09:00"))
        assert s.session == MarketSession.PRE_MARKET
        assert s.is_trading_day is True

    def test_intraday_morning(self):
        """交易时段上午"""
        s = MarketSessionResolver().resolve(now=_dt(f"{TRADING_MONDAY} 10:30"))
        assert s.session == MarketSession.INTRADAY

    def test_intraday_afternoon(self):
        """交易时段下午"""
        s = MarketSessionResolver().resolve(now=_dt(f"{TRADING_MONDAY} 14:00"))
        assert s.session == MarketSession.INTRADAY

    def test_intraday_lunch_break(self):
        """午休视为盘中"""
        s = MarketSessionResolver().resolve(now=_dt(f"{TRADING_MONDAY} 11:45"))
        assert s.session == MarketSession.INTRADAY

    def test_post_market_pending(self):
        """收盘后日线未完成（16:00前）"""
        s = MarketSessionResolver().resolve(now=_dt(f"{TRADING_MONDAY} 15:30"))
        assert s.session == MarketSession.POST_MARKET_PENDING
        assert s.daily_data_available is False

    def test_post_market_ready(self):
        """收盘后日线已完成（16:00后）"""
        s = MarketSessionResolver().resolve(now=_dt(f"{TRADING_MONDAY} 17:00"))
        assert s.session == MarketSession.POST_MARKET_READY
        assert s.daily_data_available is True

    def test_non_trading_day(self):
        """周末非交易日"""
        s = MarketSessionResolver().resolve(now=_dt(f"{SUNDAY} 10:00"))
        assert s.session == MarketSession.NON_TRADING_DAY
        assert s.is_trading_day is False

    def test_latest_completed_present(self):
        """最近完成交易日始终给出"""
        s = MarketSessionResolver().resolve(now=_dt(f"{TRADING_MONDAY} 10:30"))
        assert s.latest_completed_trade_date  # 非空

    def test_render_marks_session(self):
        """输出必须标记市场阶段"""
        s = MarketSessionResolver().resolve(now=_dt(f"{TRADING_MONDAY} 10:30"))
        text = s.render()
        assert "市场阶段" in text and "交易时段" in text


class TestSessionAwareRouting:
    """今天有什么好票不能固定映射INTRADAY"""

    def test_intraday_during_trading(self):
        """交易时段 → INTRADAY_SCAN"""
        sess, route = MarketSessionResolver().route_for(
            "今天有什么好票", now=_dt(f"{TRADING_MONDAY} 10:30"))
        assert sess == MarketSession.INTRADAY
        assert route == "INTRADAY_SCAN"

    def test_pre_market_uses_eod(self):
        """开盘前 → EOD_SCAN（最近完成日线，非今日盘中）"""
        sess, route = MarketSessionResolver().route_for(
            "今天有什么好票", now=_dt(f"{TRADING_MONDAY} 09:00"))
        assert sess == MarketSession.PRE_MARKET
        assert "EOD_SCAN" in route

    def test_non_trading_uses_eod(self):
        """周末 → EOD_SCAN"""
        sess, route = MarketSessionResolver().route_for(
            "今天有什么好票", now=_dt(f"{SUNDAY} 10:00"))
        assert sess == MarketSession.NON_TRADING_DAY
        assert "EOD_SCAN" in route

    def test_post_market_pending_keeps_intraday_snapshot(self):
        """收盘后待完成 → 最终盘中快照（明确日线未完成）"""
        sess, route = MarketSessionResolver().route_for(
            "今天有什么好票", now=_dt(f"{TRADING_MONDAY} 15:30"))
        assert sess == MarketSession.POST_MARKET_PENDING
        assert "INTRADAY_SCAN" in route

    def test_post_market_ready_uses_eod(self):
        """收盘后日线完成 → EOD_SCAN（当日收盘数据）"""
        sess, route = MarketSessionResolver().route_for(
            "今天有什么好票", now=_dt(f"{TRADING_MONDAY} 17:00"))
        assert sess == MarketSession.POST_MARKET_READY
        assert "EOD_SCAN" in route
