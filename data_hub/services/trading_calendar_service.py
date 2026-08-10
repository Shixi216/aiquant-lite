"""统一交易日历服务 TradingCalendarService（任务书 2026-08 规则1-3）

供 stock_report、ScannerFreshnessGuard 及其他新鲜度检查共用。
最近交易日计算必须满足：
- is_open = 1（开市日）
- cal_date <= 当前本地日期（不含未来）
- cal_date <= data_cutoff 日期（若提供）
- cal_date 降序取最大值

禁止：
- 依赖 Tushare 返回顺序（升序/降序都可能变化）
- 用 iloc[0] / iloc[-1] 推断最近交易日
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional

TZ = timezone(timedelta(hours=8))
_OPEN_TIME = datetime.strptime("09:30", "%H:%M").time()  # A股开盘时间
_CLOSE_TIME = datetime.strptime("15:00", "%H:%M").time()  # A股收盘时间


class TradingCalendarService:
    """统一交易日历服务（线程安全 + 短时缓存）"""

    _instance: Optional["TradingCalendarService"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "TradingCalendarService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._cache: dict[str, list[dict]] = {}   # cache_key -> [{cal_date, is_open}]
        self._cache_time: dict[str, datetime] = {}
        self._cache_ttl = timedelta(hours=1)       # 日历缓存1小时
        self._exchange = "SSE"                      # 交易所（缓存键组成）
        self._calendar_type = "trade_cal"           # 日历类型（缓存键组成）
        self._data_source_version = "tushare-v1"    # 数据源版本（缓存键组成）

    def _now(self) -> datetime:
        """当前时间（可被测试 patch 的时钟抽象）"""
        return datetime.now(tz=TZ)

    # ------------------------------------------------------------------
    # 概念1：current_or_latest_open_date（盘中/状态用）
    # ------------------------------------------------------------------
    def current_or_latest_open_date(
        self,
        data_cutoff: Optional[date | datetime] = None,
        as_str: bool = False,
    ) -> Optional[date | str]:
        """当前（或最近）开市日。

        用于盘中行情和交易日状态判断。
        - 当天开市 → 可返回当天（即使日线未完成）
        - 非开市日 → 返回最近开市日
        不要求日线已完成。
        """
        cutoff_date: Optional[date] = None
        if isinstance(data_cutoff, datetime):
            cutoff_date = data_cutoff.date()
        elif isinstance(data_cutoff, date):
            cutoff_date = data_cutoff

        today = self._now().date()
        upper = cutoff_date if (cutoff_date and cutoff_date < today) else today

        start = upper - timedelta(days=30)
        cal = self._fetch_calendar(start, upper)
        open_days = [
            item for item in cal
            if item["is_open"] and item["cal_date"] <= upper
        ]
        if not open_days:
            return None
        latest = max(open_days, key=lambda x: x["cal_date"])["cal_date"]
        return latest.strftime("%Y%m%d") if as_str else latest

    # ------------------------------------------------------------------
    # 概念2：latest_completed_trade_date（日线/指标/快照/回测用）
    # ------------------------------------------------------------------
    def latest_completed_trade_date(
        self,
        data_cutoff: Optional[date | datetime] = None,
        as_str: bool = False,
        data_available_date: Optional[date] = None,
        refresh_status: Optional[str] = None,
    ) -> tuple[Optional[date], Optional[str]]:
        """最近已完成交易日（日线实际可用）。

        综合判断：
        - 当前时间
        - data_cutoff
        - 数据源实际返回的最新 trade_date（data_available_date）
        - 刷新状态（refresh_status: FRESH/DEGRADED/REFRESH_PENDING）

        返回 (date, status)：
        - date: 最近完成交易日
        - status: FRESH（数据已入库）/ REFRESH_PENDING（刷新中，返回前一完成日）/
                  DEGRADED（降级，返回前一完成日）
        开盘前和盘中必须返回前一交易日，不得只凭 is_open=1 判定完成。
        """
        cutoff_date: Optional[date] = None
        if isinstance(data_cutoff, datetime):
            cutoff_date = data_cutoff.date()
        elif isinstance(data_cutoff, date):
            cutoff_date = data_cutoff

        today = self._now().date()
        now = self._now()

        # 上限 = min(今天, cutoff)
        upper = cutoff_date if (cutoff_date and cutoff_date < today) else today

        start = upper - timedelta(days=30)
        cal = self._fetch_calendar(start, upper)
        open_days = [
            item for item in cal
            if item["is_open"] and item["cal_date"] <= upper
        ]
        if not open_days:
            return None, "DEGRADED"

        # 候选最近开市日
        latest_open = max(open_days, key=lambda x: x["cal_date"])["cal_date"]

        # 数据源实际可用日期（若提供，优先作为完成日上限）
        if data_available_date is not None:
            latest_open = min(latest_open, data_available_date)

        # 刷新状态降级
        if refresh_status in ("REFRESH_PENDING", "DEGRADED", "FAILED"):
            # 当日数据未完成 → 返回前一完成交易日
            prev_open = [
                item["cal_date"] for item in open_days
                if item["cal_date"] < latest_open
            ]
            if not prev_open:
                return None, refresh_status
            return max(prev_open), refresh_status

        # 显式历史时点也要保留盘前语义，不能因日期早于今天就把当天视为已完成。
        explicit_premarket_cutoff = (
            isinstance(data_cutoff, datetime)
            and upper == latest_open
            and data_cutoff.time() < _OPEN_TIME
        )
        if explicit_premarket_cutoff:
            prev_open = [
                item["cal_date"] for item in open_days
                if item["cal_date"] < latest_open
            ]
            if not prev_open:
                return None, "REFRESH_PENDING"
            return max(prev_open), "REFRESH_PENDING"

        # 开盘前/盘中判断（不写死15:00，用数据可用性）
        # 仅当 cutoff == 今天（或 cutoff 极接近当前）时才考虑"当日未完成"
        now = self._now()
        is_recent = (upper >= today) or (
            cutoff_date is not None and cutoff_date == today
        )
        is_upper_open_today = is_recent and upper == latest_open and any(
            item["cal_date"] == upper and item["is_open"] for item in open_days
        )
        if is_upper_open_today:
            # upper 当天开市：只有数据源确认已返回当日数据才视为完成
            if data_available_date is not None and data_available_date >= upper:
                return latest_open, "FRESH"          # 当日数据已入库
            # 未提供数据可用日期或未到 → 视为未完成，返回前一交易日
            prev_open = [
                item["cal_date"] for item in open_days
                if item["cal_date"] < latest_open
            ]
            if not prev_open:
                return None, "REFRESH_PENDING"
            return max(prev_open), "REFRESH_PENDING"

        # 历史 cutoff（非当天）→ 最近开市日即完成日
        return latest_open, "FRESH"

    # ------------------------------------------------------------------
    # 兼容旧接口（latest_trade_date 默认 = completed 语义）
    # ------------------------------------------------------------------
    def latest_trade_date(
        self,
        data_cutoff: Optional[date | datetime] = None,
        as_str: bool = False,
        data_available_date: Optional[date] = None,
        refresh_status: Optional[str] = None,
    ) -> Optional[date | str]:
        """兼容入口：默认返回最近已完成交易日（旧调用方语义）。"""
        d, _ = self.latest_completed_trade_date(
            data_cutoff=data_cutoff,
            data_available_date=data_available_date,
            refresh_status=refresh_status,
        )
        if d is None:
            return None
        return d.strftime("%Y%m%d") if as_str else d

    # ------------------------------------------------------------------
    # 日历拉取（带缓存，键含 exchange/类型/日期边界/数据源版本）
    # ------------------------------------------------------------------
    def _fetch_calendar(self, start: date, end: date) -> list[dict]:
        """按年月桶整月拉取并缓存，返回时按请求窗口截取。

        桶粒度：月份。任何查询窗口（±5天/往前30天）落在同一月内时
        共享同一份缓存，避免每窗口各打一次数据接口。
        """
        # 请求窗口覆盖的月份范围（含首尾月）
        first_bucket = start.year * 12 + start.month - 1
        last_bucket = end.year * 12 + end.month - 1

        # 确保所有涉及的月份桶都已缓存
        for bucket in range(first_bucket, last_bucket + 1):
            year, month = divmod(bucket, 12)
            year, month = year, month + 1
            self._ensure_bucket_cached(year, month)

        # 从各桶汇总并过滤窗口
        result = []
        for bucket in range(first_bucket, last_bucket + 1):
            year, month = divmod(bucket, 12)
            month = month + 1
            cache_key = self._bucket_key(year, month)
            for item in self._cache.get(cache_key, []):
                if start <= item["cal_date"] <= end:
                    result.append(item)
        return result

    def _bucket_key(self, year: int, month: int) -> str:
        return "\x1f".join([
            self._exchange,
            self._calendar_type,
            str(year * 12 + month - 1),
            str(year * 12 + month - 1),
            self._data_source_version,
        ])

    def _ensure_bucket_cached(self, year: int, month: int) -> None:
        """确保某年月的日历已缓存（未缓存则整月拉取）"""
        cache_key = self._bucket_key(year, month)
        now = self._now()
        if cache_key in self._cache:
            if now - self._cache_time[cache_key] < self._cache_ttl:
                return  # 缓存有效

        # 整月拉取
        month_start = date(year, month, 1)
        if month == 12:
            month_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(year, month + 1, 1) - timedelta(days=1)

        import tushare as ts
        from config.settings import settings

        ts.set_token(settings.tushare_token.strip())
        pro = ts.pro_api()
        df = pro.trade_cal(
            exchange=self._exchange,
            start_date=month_start.strftime("%Y%m%d"),
            end_date=month_end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return

        items = []
        for _, row in df.iterrows():
            try:
                d = datetime.strptime(str(row["cal_date"]), "%Y%m%d").date()
            except (ValueError, TypeError):
                continue
            items.append({
                "cal_date": d,
                "is_open": bool(int(row.get("is_open", 0))),
            })

        self._cache[cache_key] = items
        self._cache_time[cache_key] = now

    # ------------------------------------------------------------------
    # 便捷：最近 N 个交易日（供测试/回放）
    # ------------------------------------------------------------------
    def recent_trade_dates(
        self, n: int = 5, data_cutoff: Optional[date | datetime] = None,
    ) -> list[date]:
        """最近 n 个交易日（降序）"""
        cutoff_date: Optional[date] = None
        if isinstance(data_cutoff, datetime):
            cutoff_date = data_cutoff.date()
        elif isinstance(data_cutoff, date):
            cutoff_date = data_cutoff

        today = self._now().date()
        upper = cutoff_date if (cutoff_date and cutoff_date < today) else today
        start = upper - timedelta(days=max(30, n * 7))
        cal = self._fetch_calendar(start, upper)
        open_days = sorted(
            [item["cal_date"] for item in cal if item["is_open"] and item["cal_date"] <= upper],
            reverse=True,
        )
        return open_days[:n]

    def is_open_day(self, d: date) -> bool:
        """指定日期是否为开市日"""
        cal = self._fetch_calendar(d - timedelta(days=5), d + timedelta(days=5))
        for item in cal:
            if item["cal_date"] == d:
                return bool(item["is_open"])
        return False

    def clear_cache(self) -> None:
        """清空缓存（测试用）"""
        self._cache.clear()
        self._cache_time.clear()


# 单例便捷函数
def latest_trade_date(
    data_cutoff: Optional[date | datetime] = None,
    as_str: bool = False,
) -> Optional[date | str]:
    """最近交易日（全局便捷入口）"""
    return TradingCalendarService().latest_trade_date(data_cutoff=data_cutoff, as_str=as_str)
