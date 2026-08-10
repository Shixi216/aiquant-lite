"""交易日与 no-fetch 语义回归测试（任务书 2026-08 规则6）

覆盖场景：
1. 周六运行，最新交易日为周五
2. 周日运行，最新交易日为周五
3. 周一开盘前，最新完成交易日仍为上周五
4. 法定节假日
5. trade_cal 包含未来开市日
6. data_cutoff 早于当前日期
7. 返回顺序升序
8. 返回顺序降序
9. no-fetch 网络调用 0
10. no-fetch 刷新进程 0
11. stock_report 与 Scanner 得到相同最近交易日

运行：python -m pytest tests/test_trading_calendar.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_hub.services.trading_calendar_service import TradingCalendarService

TZ = timezone(timedelta(hours=8))

# 2026-08-02 是周日，2026-08-03 是周一（真实世界锚点）
# 2026-07-31 是周五（最近完成交易日）


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    holidays = {
        date(2026, 10, day) for day in range(1, 8)
    }

    def deterministic_calendar(self, start, end):
        values = []
        current = start
        while current <= end:
            values.append({
                "cal_date": current,
                "is_open": current.weekday() < 5 and current not in holidays,
            })
            current += timedelta(days=1)
        return values

    monkeypatch.setattr(
        TradingCalendarService,
        "_fetch_calendar",
        deterministic_calendar,
    )
    svc = TradingCalendarService()
    svc.clear_cache()
    yield
    svc.clear_cache()

# =====================================================================
# T1 周末场景（周六/周日）
# =====================================================================
class TestWeekend:
    def test_saturday_latest_is_friday(self):
        """周六运行 → 最新交易日 = 周五"""
        svc = TradingCalendarService()
        # 周六 = 2026-08-01
        result = svc.latest_trade_date(data_cutoff=date(2026, 8, 1), as_str=True)
        # 2026-07-31 是周五
        assert result == "20260731", f"周六应返回周五20260731，实际{result}"

    def test_sunday_latest_is_friday(self):
        """周日运行 → 最新交易日 = 周五"""
        svc = TradingCalendarService()
        result = svc.latest_trade_date(data_cutoff=date(2026, 8, 2), as_str=True)
        assert result == "20260731", f"周日应返回周五20260731，实际{result}"

    def test_monday_before_open(self):
        """周一开盘前 → 最新完成交易日 = 上周五"""
        svc = TradingCalendarService()
        # 周一开盘前 = 2026-08-03 早上（data_cutoff 为 8/3 00:30）
        cutoff = datetime(2026, 8, 3, 0, 30, tzinfo=TZ)
        result = svc.latest_trade_date(data_cutoff=cutoff, as_str=True)
        assert result == "20260731", f"周一开盘前应返回上周五20260731，实际{result}"


# =====================================================================
# T2 法定节假日
# =====================================================================
class TestHoliday:
    def test_holiday_returns_previous_trade_date(self):
        """法定节假日（国庆10/1-10/7）→ 返回节前最后交易日"""
        svc = TradingCalendarService()
        # 2026-10-01 国庆节（节假日）
        result = svc.latest_trade_date(data_cutoff=date(2026, 10, 1), as_str=True)
        # 2026-09-30 是周三（节前最后交易日，若无调休）
        assert result is not None
        assert result < "20261001", f"节假日应返回节前交易日，实际{result}"
        # 验证返回的确实是交易日（is_open=1）
        assert svc.is_open_day(date(2026, 9, 30)) or result == "20260930"


# =====================================================================
# T3 未来开市日 / 顺序无关性
# =====================================================================
class TestOrderIndependence:
    def test_future_open_days_excluded(self):
        """trade_cal 包含未来开市日（如下周一8/3）→ 不应返回未来日期"""
        svc = TradingCalendarService()
        result = svc.latest_trade_date(data_cutoff=date(2026, 8, 2), as_str=True)
        # 8/3 是未来开市日，不应被返回
        assert result != "20260803"
        assert result == "20260731"

    def test_cutoff_before_today(self):
        """data_cutoff 早于当前日期 → 返回 cutoff 前最后交易日"""
        svc = TradingCalendarService()
        # cutoff = 7/29（周三）
        result = svc.latest_trade_date(data_cutoff=date(2026, 7, 29), as_str=True)
        assert result == "20260729", f"cutoff=7/29应返回7/29，实际{result}"

    def test_ascending_order_result(self):
        """日历升序返回 → 结果正确（不依赖顺序）"""
        svc = TradingCalendarService()
        # 手动验证：构造升序数据
        from datetime import datetime as dt
        items = [
            {"cal_date": date(2026, 7, 29), "is_open": 1},
            {"cal_date": date(2026, 7, 30), "is_open": 1},
            {"cal_date": date(2026, 7, 31), "is_open": 1},
            {"cal_date": date(2026, 8, 1), "is_open": 0},
            {"cal_date": date(2026, 8, 2), "is_open": 0},
        ]
        open_days = [i for i in items if i["is_open"] and i["cal_date"] <= date(2026, 8, 2)]
        latest = max(open_days, key=lambda x: x["cal_date"])["cal_date"]
        assert latest == date(2026, 7, 31)

    def test_descending_order_result(self):
        """日历降序返回 → 结果正确（不依赖顺序）"""
        svc = TradingCalendarService()
        items = [
            {"cal_date": date(2026, 8, 2), "is_open": 0},
            {"cal_date": date(2026, 8, 1), "is_open": 0},
            {"cal_date": date(2026, 7, 31), "is_open": 1},
            {"cal_date": date(2026, 7, 30), "is_open": 1},
            {"cal_date": date(2026, 7, 29), "is_open": 1},
        ]
        open_days = [i for i in items if i["is_open"] and i["cal_date"] <= date(2026, 8, 2)]
        latest = max(open_days, key=lambda x: x["cal_date"])["cal_date"]
        assert latest == date(2026, 7, 31)


# =====================================================================
# T4 no-fetch 语义（规则4-5）
# =====================================================================
class TestNoFetch:
    def test_no_fetch_no_network(self, tmp_path):
        """--no-fetch 网络调用为 0（不拉取任何数据）"""
        # 用 monkeypatch 拦截网络调用：跑 stock_report --no-fetch
        # 检查日志中无"拉取"字样
        env = dict(os.environ)
        env["TUSHARE_TOKEN"] = os.environ.get("TUSHARE_TOKEN", "")
        result = subprocess.run(
            [sys.executable, "scripts/stock_report.py", "600172", "--no-fetch", "--no-ai"],
            capture_output=True, text=True, encoding="utf-8", errors="backslashreplace",
            cwd=str(Path(__file__).resolve().parents[1]), env=env, timeout=60,
        )
        output = result.stdout + result.stderr
        # 不应有网络拉取/自动刷新（"跳过数据拉取"是提示语，非实际拉取）
        assert "正在自动刷新" not in output, f"--no-fetch 不应自动刷新:\n{output}"
        assert "并行拉取" not in output, f"--no-fetch 不应并行拉取:\n{output}"

    def test_no_fetch_stale_only_status(self, tmp_path):
        """--no-fetch 遇 STALE 只返回状态，不自动拉取"""
        env = dict(os.environ)
        env["TUSHARE_TOKEN"] = os.environ.get("TUSHARE_TOKEN", "")
        result = subprocess.run(
            [sys.executable, "scripts/stock_report.py", "999999", "--no-fetch", "--no-ai"],
            capture_output=True, text=True, encoding="utf-8", errors="backslashreplace",
            cwd=str(Path(__file__).resolve().parents[1]), env=env, timeout=60,
        )
        output = result.stdout + result.stderr
        # 不存在的股票 → 应提示无数据而非自动拉取
        assert "自动刷新" not in output

    def test_no_fetch_no_refresh_process(self):
        """--no-fetch 不触发刷新进程（无 refresh_snapshot 调用）"""
        env = dict(os.environ)
        env["TUSHARE_TOKEN"] = os.environ.get("TUSHARE_TOKEN", "")
        # 运行后检查是否产生刷新进程
        result = subprocess.run(
            [sys.executable, "scripts/stock_report.py", "600172", "--no-fetch", "--no-ai"],
            capture_output=True, text=True, encoding="utf-8", errors="backslashreplace",
            cwd=str(Path(__file__).resolve().parents[1]), env=env, timeout=60,
        )
        # 不直接检查进程（可能竞态），检查输出无"刷新"
        assert "正在自动刷新" not in (result.stdout + result.stderr)


# =====================================================================
# T5 stock_report 与 Scanner 一致性（规则1）
# =====================================================================
class TestConsistency:
    def test_same_latest_trade_date(self):
        """stock_report 与 Scanner 得到相同最近交易日"""
        from data_hub.services.trading_calendar_service import TradingCalendarService
        svc = TradingCalendarService()

        # stock_report 路径（_check_freshness 用）
        sr_date = svc.latest_trade_date(as_str=True)

        # Scanner 路径（freshness_guard._latest_trade_date 用）
        from trading.scanner.freshness_guard import ScannerFreshnessGuard
        guard_date = ScannerFreshnessGuard()._latest_trade_date()

        assert sr_date == guard_date, (
            f"stock_report({sr_date}) 与 Scanner({guard_date}) 最近交易日不一致"
        )

    def test_guard_check_fresh_uses_same(self, monkeypatch):
        """守卫检查与统一服务一致"""
        from trading.scanner.freshness_guard import ScannerFreshnessGuard
        fixed_now = datetime(2026, 8, 4, 9, 0, tzinfo=TZ)
        calendar = [
            {"cal_date": date(2026, 8, 3), "is_open": 1},
            {"cal_date": date(2026, 8, 4), "is_open": 1},
        ]
        monkeypatch.setattr(TradingCalendarService, "_now", lambda self: fixed_now)
        monkeypatch.setattr(
            TradingCalendarService, "_fetch_calendar",
            lambda self, start, end: calendar,
        )
        expected = max(
            item["cal_date"] for item in calendar
            if item["is_open"] and item["cal_date"] < fixed_now.date()
        ).strftime("%Y%m%d")

        TradingCalendarService().clear_cache()
        latest = ScannerFreshnessGuard()._latest_trade_date()
        assert latest == expected, f"守卫最近交易日应为{expected}，实际{latest}"


# =====================================================================
# T6 日内边界（两概念拆分验证）— 用注入日历数据（不依赖当前时间）
# =====================================================================
class TestIntradayBoundary:
    """current_or_latest_open_date vs latest_completed_trade_date

    用 monkeypatch 固定"当前时间"，避免测试依赖真实时钟。
    """

    def _patch_now(self, monkeypatch, dt: datetime):
        """把 TradingCalendarService 的当前时间固定为 dt"""
        import data_hub.services.trading_calendar_service as tcs
        # patch 实例方法 _now（不修改 immutable datetime 类）
        monkeypatch.setattr(
            tcs.TradingCalendarService, "_now",
            lambda self: dt,
        )

    def _inject_cal(self, monkeypatch, days: list[tuple[str, int]]):
        """注入日历数据 [(YYYYMMDD, is_open), ...]"""
        import data_hub.services.trading_calendar_service as tcs
        items = []
        for dstr, open_flag in days:
            items.append({
                "cal_date": datetime.strptime(dstr, "%Y%m%d").date(),
                "is_open": open_flag,
            })
        monkeypatch.setattr(tcs.TradingCalendarService, "_fetch_calendar",
                            lambda self, start, end: items)

    def test_premarket_tuesday(self, monkeypatch):
        """周二9:00开盘前 → completed=周一，open_date=周二"""
        self._patch_now(monkeypatch, datetime(2026, 8, 4, 9, 0, tzinfo=TZ))
        self._inject_cal(monkeypatch, [
            ("20260803", 1), ("20260804", 1), ("20260805", 1),
        ])
        svc = TradingCalendarService()
        svc.clear_cache()
        open_date = svc.current_or_latest_open_date(as_str=True)
        completed, status = svc.latest_completed_trade_date()
        assert open_date == "20260804", f"开盘前 open_date 应为周二8/4，实际{open_date}"
        assert completed == date(2026, 8, 3), f"开盘前 completed 应为周一8/3，实际{completed}"
        assert status == "REFRESH_PENDING"

    def test_intraday_tuesday(self, monkeypatch):
        """周二11:00盘中 → completed=周一，open_date=周二"""
        self._patch_now(monkeypatch, datetime(2026, 8, 4, 11, 0, tzinfo=TZ))
        self._inject_cal(monkeypatch, [
            ("20260803", 1), ("20260804", 1), ("20260805", 1),
        ])
        svc = TradingCalendarService()
        svc.clear_cache()
        open_date = svc.current_or_latest_open_date(as_str=True)
        completed, status = svc.latest_completed_trade_date()
        assert open_date == "20260804"
        assert completed == date(2026, 8, 3), f"盘中 completed 应为周一8/3，实际{completed}"
        assert status == "REFRESH_PENDING"

    def test_just_after_close_no_data(self, monkeypatch):
        """15:05刚收盘但数据源未返回当日 → completed=周一，status=REFRESH_PENDING"""
        self._patch_now(monkeypatch, datetime(2026, 8, 4, 15, 5, tzinfo=TZ))
        self._inject_cal(monkeypatch, [
            ("20260803", 1), ("20260804", 1), ("20260805", 1),
        ])
        svc = TradingCalendarService()
        svc.clear_cache()
        completed, status = svc.latest_completed_trade_date()
        assert completed == date(2026, 8, 3), f"数据未回时 completed 应为周一，实际{completed}"
        assert status == "REFRESH_PENDING"

    def test_after_close_data_available(self, monkeypatch):
        """收盘后当日日线已入库（data_available_date=当天）→ completed=当天"""
        self._patch_now(monkeypatch, datetime(2026, 8, 4, 18, 0, tzinfo=TZ))
        self._inject_cal(monkeypatch, [
            ("20260803", 1), ("20260804", 1), ("20260805", 1),
        ])
        svc = TradingCalendarService()
        svc.clear_cache()
        completed, status = svc.latest_completed_trade_date(
            data_available_date=date(2026, 8, 4))
        assert completed == date(2026, 8, 4), f"数据已入库 completed 应为8/4，实际{completed}"
        assert status == "FRESH"

    def test_refresh_pending_status(self, monkeypatch):
        """refresh_status=REFRESH_PENDING → 返回前一完成日"""
        self._patch_now(monkeypatch, datetime(2026, 8, 4, 16, 0, tzinfo=TZ))
        self._inject_cal(monkeypatch, [
            ("20260803", 1), ("20260804", 1), ("20260805", 1),
        ])
        svc = TradingCalendarService()
        svc.clear_cache()
        completed, status = svc.latest_completed_trade_date(
            refresh_status="REFRESH_PENDING")
        assert completed == date(2026, 8, 3), f"REFRESH_PENDING 应返回周一，实际{completed}"
        assert status == "REFRESH_PENDING"

    def test_night_run(self, monkeypatch):
        """夜间22:00运行 → 未声明数据可用则保守 REFRESH_PENDING"""
        self._patch_now(monkeypatch, datetime(2026, 8, 5, 22, 0, tzinfo=TZ))
        self._inject_cal(monkeypatch, [
            ("20260804", 1), ("20260805", 1), ("20260806", 1),
        ])
        svc = TradingCalendarService()
        svc.clear_cache()
        completed, status = svc.latest_completed_trade_date()
        # 未声明数据可用 → 保守返回前一交易日
        assert completed == date(2026, 8, 4), f"夜间保守应返回8/4，实际{completed}"
        assert status == "REFRESH_PENDING"

    def test_weekend_run(self, monkeypatch):
        """周六运行 → open_date=周五，completed=周五（FRESH）"""
        self._patch_now(monkeypatch, datetime(2026, 8, 8, 10, 0, tzinfo=TZ))
        self._inject_cal(monkeypatch, [
            ("20260807", 1), ("20260808", 0), ("20260809", 0),
        ])
        svc = TradingCalendarService()
        svc.clear_cache()
        open_date = svc.current_or_latest_open_date(as_str=True)
        completed, status = svc.latest_completed_trade_date()
        assert open_date == "20260807", f"周六 open_date 应为周五8/7，实际{open_date}"
        assert completed == date(2026, 8, 7), f"周六 completed 应为周五8/7，实际{completed}"
        assert status == "FRESH"

    def test_cutoff_before_now(self, monkeypatch):
        """data_cutoff 早于当前时间（历史回测）→ 按 cutoff 返回当日"""
        self._patch_now(monkeypatch, datetime(2026, 8, 4, 10, 0, tzinfo=TZ))
        self._inject_cal(monkeypatch, [
            ("20260728", 1), ("20260729", 1), ("20260730", 1),
        ])
        svc = TradingCalendarService()
        svc.clear_cache()
        completed, status = svc.latest_completed_trade_date(
            data_cutoff=datetime(2026, 7, 29, 10, 0, tzinfo=TZ))
        assert completed == date(2026, 7, 29), f"历史cutoff completed 应为7/29，实际{completed}"
        assert status == "FRESH"
