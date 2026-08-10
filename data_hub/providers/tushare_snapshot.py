"""Tushare 盘后全市场快照主源 Provider

规则：
1. 每个交易日收盘后按 trade_date 批量调用 daily/daily_basic/moneyflow/stk_limit
2. 单接口按日期一次拉取全市场，禁止逐股请求
3. 腾讯/AKShare 仅盘中补充和失败备用
4. BaoStock 仅历史行情校验
5. 三时间戳区分：trade_date(实际交易日期)/snapshot_time(快照生成时间)/collected_at(采集时间)
6. 非交易日不生成虚假 trade_date
7. 质量校验：覆盖率≥99%、daily与daily_basic按ts_code对齐、moneyflow缺失单独标记
"""
from __future__ import annotations

from database.db import open_database

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

TZ = timezone(timedelta(hours=8))


@dataclass
class TushareSnapshotResult:
    trade_date: str                      # 实际交易日期 YYYYMMDD
    snapshot_time: datetime              # 快照生成时间
    collected_at: datetime               # 数据采集时间
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    daily_basic: pd.DataFrame = field(default_factory=pd.DataFrame)
    moneyflow: pd.DataFrame = field(default_factory=pd.DataFrame)
    stk_limit: pd.DataFrame = field(default_factory=pd.DataFrame)
    request_count: int = 0
    row_counts: dict = field(default_factory=dict)
    coverage_ratio: float = 0.0          # 股票池覆盖率
    aligned: bool = False                # daily/daily_basic 对齐
    moneyflow_missing: bool = False      # moneyflow 缺失标记
    error: str = ""
    source: str = "Tushare"


class TushareSnapshotProvider:
    provider_name = "Tushare"

    def __init__(self):
        import tushare as ts
        from config.settings import settings
        ts.set_token(settings.tushare_token.strip())
        self.pro = ts.pro_api()
        self.settings = settings

    # ------------------------------------------------------------------
    # 规则6：获取最近交易日（交易日历，非交易日返回最近的开市日）
    # ------------------------------------------------------------------
    def latest_trade_date(self, as_of: datetime | None = None) -> str | None:
        """最近交易日（统一 TradingCalendarService，不依赖返回顺序）"""
        from data_hub.services.trading_calendar_service import TradingCalendarService
        as_of = as_of or datetime.now(tz=TZ)
        return TradingCalendarService().latest_trade_date(
            data_cutoff=as_of, as_str=True,
        )

    def is_trade_date(self, date_str: str) -> bool:
        cal = self.pro.trade_cal(
            exchange="SSE",
            start_date=date_str,
            end_date=date_str,
            is_open="1",
        )
        return not cal.empty

    # ------------------------------------------------------------------
    # 规则1+2：按 trade_date 批量拉取全市场（单接口一次请求，禁逐股）
    # ------------------------------------------------------------------
    def fetch_snapshot(self, trade_date: str | None = None) -> TushareSnapshotResult:
        now = datetime.now(tz=TZ)

        # 规则6：确定实际交易日
        if trade_date is None:
            trade_date = self.latest_trade_date()
        if trade_date is None:
            return TushareSnapshotResult(
                trade_date="", snapshot_time=now, collected_at=now,
                error="无法确定最近交易日", source=self.provider_name,
            )
        # 非交易日保护：若指定日期非交易日，回退到最近交易日
        if not self.is_trade_date(trade_date):
            trade_date = self.latest_trade_date() or trade_date

        result = TushareSnapshotResult(
            trade_date=trade_date,
            snapshot_time=now,
            collected_at=now,
            source=self.provider_name,
        )

        # 1) daily（全市场日线）
        try:
            df = self.pro.daily(trade_date=trade_date)
            result.daily = df
            result.request_count += 1
            result.row_counts["daily"] = len(df)
        except Exception as exc:
            result.error = f"daily失败: {str(exc)[:80]}"

        # 2) daily_basic（估值/换手）
        try:
            df2 = self.pro.daily_basic(
                trade_date=trade_date,
                fields="ts_code,trade_date,close,turnover_rate,volume_ratio,pe_ttm,pb,total_mv,circ_mv",
            )
            result.daily_basic = df2
            result.request_count += 1
            result.row_counts["daily_basic"] = len(df2)
        except Exception as exc:
            result.error += f" | daily_basic失败: {str(exc)[:60]}"

        # 3) moneyflow（主力资金流）— 规则7：缺失单独标记
        try:
            df3 = self.pro.moneyflow(trade_date=trade_date)
            result.moneyflow = df3
            result.request_count += 1
            result.row_counts["moneyflow"] = len(df3)
        except Exception as exc:
            result.moneyflow_missing = True
            result.error += f" | moneyflow缺失: {str(exc)[:60]}"

        # 4) stk_limit（涨跌停价）
        try:
            df4 = self.pro.stk_limit(trade_date=trade_date)
            result.stk_limit = df4
            result.request_count += 1
            result.row_counts["stk_limit"] = len(df4)
        except Exception as exc:
            result.error += f" | stk_limit缺失: {str(exc)[:60]}"

        # 规则7：质量校验
        self._validate(result)
        return result

    # ------------------------------------------------------------------
    # 规则7：质量校验（覆盖率≥99%、对齐、记录）
    # ------------------------------------------------------------------
    def _validate(self, result: TushareSnapshotResult) -> None:
        # 股票池
        import duckdb
        from pathlib import Path
        db_path = Path(self.settings.opc_database_path)
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parents[2] / db_path
        con = open_database(str(db_path), read_only=True)
        universe = con.execute(
            "SELECT COUNT(*) FROM stock_universe WHERE listing_status='ACTIVE'"
        ).fetchone()[0]
        con.close()

        # 覆盖率
        if universe > 0 and not result.daily.empty:
            result.coverage_ratio = len(result.daily) / universe

        # 对齐检查：daily 与 daily_basic 的 ts_code 交集
        if not result.daily.empty and not result.daily_basic.empty:
            daily_codes = set(result.daily["ts_code"])
            basic_codes = set(result.daily_basic["ts_code"])
            overlap = len(daily_codes & basic_codes) / max(len(daily_codes), 1)
            result.aligned = overlap >= 0.99


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    start = time.perf_counter()
    p = TushareSnapshotProvider()
    print("最近交易日:", p.latest_trade_date())
    r = p.fetch_snapshot()
    print(f"交易日期: {r.trade_date}")
    print(f"快照生成: {r.snapshot_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"采集时间: {r.collected_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"请求数: {r.request_count} | 行数: {r.row_counts}")
    print(f"覆盖率: {r.coverage_ratio:.1%} | 对齐: {r.aligned} | moneyflow缺失: {r.moneyflow_missing}")
    print(f"耗时: {time.perf_counter()-start:.1f}s")
