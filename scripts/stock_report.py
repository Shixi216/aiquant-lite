"""个股五维研究一键分析 — 拉数据→入库→五维评分→买卖建议

用法:
  python scripts/stock_report.py 002407            # 默认分析到今天
  python scripts/stock_report.py 600172 --days 120 # 拉取120天K线

流程:
  1. 通过 Data Hub 拉取行情/财务/公告/新闻（自动入库）
  2. 技术面/基本面/资金面/情绪面/政策面 五维评分
  3. 60/40 合成 + 风险提示
  4. 输出明确的操作建议（用户授权放开，仅供参考）
"""
from __future__ import annotations

# ruff: noqa: E402

import argparse
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from config.utf8 import configure_utf8_stdio, write_utf8
from data_hub.services.daily_bars_service import DailyBarsService
from data_hub.services.announcement_service import AnnouncementService
from data_hub.services.finance_news_service import FinanceNewsService

TZ = timezone(timedelta(hours=8))


class StageTimings:
    """Thread-safe elapsed-time collector used by the CLI and Router path."""

    def __init__(self) -> None:
        self._elapsed: dict[str, float] = {}
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, stage: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            with self._lock:
                self._elapsed[stage] = self._elapsed.get(stage, 0.0) + elapsed

    def record(self, stage: str, elapsed: float) -> None:
        with self._lock:
            self._elapsed[stage] = self._elapsed.get(stage, 0.0) + elapsed

    def seconds(self, stage: str) -> float:
        with self._lock:
            return self._elapsed.get(stage, 0.0)

    def render(self, stages: list[str]) -> list[str]:
        return [f"  {stage}: {self.seconds(stage):.3f}s" for stage in stages]


def fetch_and_persist(
    symbol: str,
    days: int = 90,
    *,
    timings: StageTimings | None = None,
) -> None:
    """两阶段拉取：并行网络IO + 串行入库（避免duckdb写锁冲突）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    timings = timings or StageTimings()
    end = datetime.now(tz=TZ)
    start = end - timedelta(days=days)
    code = symbol.strip()

    # 阶段1：并行拉取（网络IO并发），返回数据+持久化函数
    def fetch_bars():
        with timings.measure("K线"):
            resp = DailyBarsService().get_daily_bars(
                code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), persist=False
            )
        return "K线", resp

    def fetch_financials():
        from data_hub.services.financial_statement_service import FinancialStatementService
        with timings.measure("财务"):
            resp = FinancialStatementService().get_financial_statement(
                code, (end - timedelta(days=550)).strftime("%Y%m%d"),
                end.strftime("%Y%m%d"), persist=False,
            )
        return "财报", resp

    def fetch_announcements():
        with timings.measure("公告"):
            try:
                resp = AnnouncementService().get_announcements(
                    code, (end - timedelta(days=60)).strftime("%Y%m%d"),
                    end.strftime("%Y%m%d"), persist=False,
                )
                return "公告", resp
            except Exception as exc:
                # 巨潮接口故障时降级：不阻塞整体分析（数据状态标记为降级）
                print(f"  ⚠️ 公告接口失败，降级跳过: {type(exc).__name__}")
                return "公告", None

    def fetch_news():
        with timings.measure("新闻"):
            resp = FinanceNewsService().get_finance_news(code, limit=15, persist=False)
        return "新闻", resp

    with timings.measure("行情"):
        print("[阶段1] 并行拉取 4 类数据（网络IO并发）...")
        results: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fn): fn.__name__ for fn in
                       [fetch_bars, fetch_financials, fetch_announcements, fetch_news]}
            for fut in as_completed(futures):
                name, resp = fut.result()
                results[name] = resp
                print(f"  ✅ {name} 拉取完成")

    # 阶段2：串行入库（避免duckdb写锁冲突）
    with timings.measure("数据库"):
        print("[阶段2] 串行入库...")
        if "K线" in results:
            _persist_records(getattr(results["K线"], "records", []))
            print("  ✅ K线 入库")
        if "财报" in results:
            _persist_records(getattr(results["财报"], "records", []))
            print("  ✅ 财报 入库")
        if "公告" in results and results["公告"] is not None:
            _persist_records(getattr(results["公告"], "records", []))
            print("  ✅ 公告 入库")
        elif "公告" in results:
            print("  ⚠️ 公告降级跳过（接口故障，用库内已有公告）")
        if "新闻" in results:
            _persist_records(getattr(results["新闻"], "records", []))
            print("  ✅ 新闻 入库")


def _persist_records(records: list) -> None:
    """把 MarketRecord 列表写入 data_records 表（去重）"""
    if not records:
        return
    from database.db import get_connection, insert_market_record, initialize_database
    initialize_database()
    with get_connection() as connection:
        for record in records:
            existing = connection.execute(
                "SELECT record_id FROM data_records WHERE content_hash = ? LIMIT 1",
                [record.content_hash],
            ).fetchone()
            if existing is None:
                insert_market_record(connection, record)
            # 同步 daily_bar 到 canonical_historical_bars（保证 load_bars 读到最新）
            if record.data_type == "daily_bar":
                _sync_daily_bar(connection, record)


def _sync_daily_bar(connection, record) -> None:
    """把 data_records 的 daily_bar 同步到 canonical_historical_bars"""
    import uuid
    data = record.data
    if not isinstance(data, dict):
        return
    trade_date = data.get("trade_date") or data.get("event_time")
    close = data.get("close")
    if not trade_date or close is None:
        return
    symbol = record.symbol
    # 日期格式标准化：20260506 → 2026-05-06
    trade_date_str = str(trade_date)[:10]
    if len(trade_date_str) == 8 and trade_date_str.isdigit():
        trade_date_str = f"{trade_date_str[:4]}-{trade_date_str[4:6]}-{trade_date_str[6:8]}"
    # 检查是否已有该日数据
    exists = connection.execute(
        "SELECT bar_id FROM canonical_historical_bars WHERE symbol=? AND trade_date=? LIMIT 1",
        [symbol, trade_date_str],
    ).fetchone()
    if exists:
        return  # 已存在，不覆盖
    connection.execute(
        """
        INSERT INTO canonical_historical_bars
        (bar_id, symbol, trade_date, event_time, data_available_time, data_cutoff,
         generated_at, adjustment_type, open, high, low, close, volume, amount,
         volume_unit, amount_unit, primary_source, source_record_ids_json,
         verification_source_ids_json, verification_status, confidence,
         content_hash, algorithm_version, raw_payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            f"hbar_{uuid.uuid4().hex[:20]}",
            symbol,
            trade_date_str,
            record.event_time,
            record.event_time,
            record.event_time,
            datetime.now(tz=TZ),
            "RAW",
            float(data.get("open") or close),
            float(data.get("high") or close),
            float(data.get("low") or close),
            float(close),
            float(data.get("volume") or 0),
            float(data.get("amount") or 0),
            data.get("volume_unit", "SHARES"),
            data.get("amount_unit", "CNY"),
            record.source_name,
            f'["{record.record_id}"]',
            f'["{record.record_id}"]',
            "VERIFIED",
            1.0,
            record.content_hash,
            "stock-report-sync-v1",
            json.dumps(data, ensure_ascii=False, default=str),
        ],
    )


def recommend(combined: float, tech: float, fund: float, cap: float, senti: float, policy: float) -> str:
    """生成操作建议（规则驱动）"""
    lines: list[str] = []

    if combined >= 0.3:
        action = "建议关注/逢低分批建仓"
        lines.append(f"综合分 {combined:.2f} ≥ 0.3，系统信号偏多。")
    elif combined >= 0.1:
        action = "中性偏多，可小仓位试探"
        lines.append(f"综合分 {combined:.2f} 在0.1~0.3之间，弱偏多。")
    elif combined <= -0.3:
        action = "建议回避/减仓，不追高"
        lines.append(f"综合分 {combined:.2f} ≤ -0.3，系统信号偏空。")
    elif combined <= -0.1:
        action = "中性偏空，观望为主"
        lines.append(f"综合分 {combined:.2f} 在-0.3~-0.1之间，弱偏空。")
    else:
        action = "中性观望，等待方向明确"
        lines.append(f"综合分 {combined:.2f} 在±0.1之间，方向不明。")

    # 分维度补充
    if tech <= -0.4:
        lines.append(f"技术面 {tech:.2f}：均线空头排列，趋势偏弱，不宜追涨。")
    elif tech >= 0.4:
        lines.append(f"技术面 {tech:.2f}：均线多头排列，趋势向好。")
    if fund <= -0.3:
        lines.append(f"基本面 {fund:.2f}：盈利/现金流偏弱，注意业绩风险。")
    elif fund >= 0.3:
        lines.append(f"基本面 {fund:.2f}：财务质量较好。")
    if cap <= -0.4:
        lines.append(f"资金面 {cap:.2f}：近期量价配合差，资金偏流出。")
    if senti <= -0.2:
        lines.append(f"情绪面 {senti:.2f}：近期负面事件偏多，警惕情绪杀。")
    elif senti >= 0.2:
        lines.append(f"情绪面 {senti:.2f}：近期有正面催化。")
    if policy <= -0.2:
        lines.append(f"政策面 {policy:.2f}：存在风险公告（减持/异动/延期等）。")

    lines.append(f"\n综合结论: {action}")
    lines.append("⚠️ 以上为系统规则计算结果，仅供研究参考，不构成投资建议；股市有风险，决策需自主。")
    return "\n".join(lines)


def _check_freshness(symbol: str, bars: list) -> bool:
    """检查K线数据是否到最近交易日。返回 True=最新，False=过期。

    永远最新数据规则：默认禁止用过期数据出报告。
    使用统一 TradingCalendarService（不依赖 Tushare 返回顺序）。
    """
    if not bars:
        return False
    from data_hub.services.trading_calendar_service import TradingCalendarService
    latest_bar_date = bars[-1].trade_date
    try:
        cal_svc = TradingCalendarService()
        last_trade = cal_svc.latest_trade_date()
        if last_trade is None:
            return True  # 无法确认交易日历 → 不阻塞
        return latest_bar_date >= last_trade
    except Exception:
        return True  # 无法确认 → 不阻塞（避免假阳性）


def main():
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="个股五维一键分析")
    parser.add_argument("symbol", help="股票代码，如002407")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--stale-ok", action="store_true")
    args = parser.parse_args()

    import httpx

    url = (
        f"http://{settings.opc_router_host}:{settings.opc_router_port}"
        "/v1/research/stock-report"
    )
    try:
        response = httpx.post(
            url,
            json={
                "symbol": args.symbol.strip(),
                "days": args.days,
                "no_fetch": args.no_fetch,
                "no_ai": args.no_ai,
                "stale_ok": args.stale_ok,
            },
            timeout=180,
            trust_env=False,
        )
        response.raise_for_status()
    except httpx.RequestError as exc:
        raise SystemExit(
            "Hermes-OPC Router不可用；stock_report拒绝降级为主库直连。"
        ) from exc
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = ""
        raise SystemExit(
            f"stock_report请求失败（HTTP {exc.response.status_code}）"
            + (f"：{detail}" if detail else "")
        ) from exc
    # A console encoding failure must never trigger a second HTTP analysis request.
    write_utf8(response.json()["report"])


if __name__ == "__main__":
    main()