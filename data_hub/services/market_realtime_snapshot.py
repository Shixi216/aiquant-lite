"""盘中全市场实时快照服务（后台维护模式）

今日/盘中类查询的正确流程：
1. 后台任务定时刷新全市场实时快照（30-60秒一次）
2. 用户查询只读最新完整快照（≤3秒、网络0、模型0）
3. 实时筛选 + 两阶段研究

设计：
- 后台刷新器：独立线程，30-60秒刷新一次，任务锁防重复
- 原子切换：新快照完全拉取成功后整体替换（用户永不看到半截数据）
- 新鲜度：快照年龄≤90秒 FRESH / 90-180秒 DEGRADED / >180秒 STALE_REALTIME
- STALE_REALTIME：触发后台刷新 + 明确提示，不用旧日线冒充今日行情

后台刷新和用户查询完全解耦：查询阶段无网络、无逐股调用。
"""
from __future__ import annotations

from database.db import open_database

import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

TZ = timezone(timedelta(hours=8))
TENCENT_HEADERS = {"User-Agent": "Mozilla/5.0"}
BATCH_SIZE = 100
CONCURRENCY = 8

# 新鲜度阈值（秒）—— 依据实测间隔（类型2：完成后等待60秒）
# 实测10次连续刷新：actual_interval = 126.0~126.1秒（min=P50=P95=max）
# 阈值 = 实测P95 + 容差
ACTUAL_INTERVAL_P95 = 126.1    # 实测P95（秒）
FRESH_MAX_AGE = 145            # ≤145秒 FRESH（P95 126.1 + 容差19秒）
DEGRADED_MAX_AGE = 290         # 145-290秒 DEGRADED（超过一轮未超两轮：2×145）
STALE_MAX_AGE = 290            # >290秒 STALE_REALTIME（超两轮或连续失败）
# 后台刷新周期（类型2：完成后等待，非固定时刻触发）
# 实际完整快照间隔 = 刷新耗时 + 等待周期 ≈ 126秒
REFRESH_INTERVAL_SECONDS = 60


@dataclass
class RealtimeQuote:
    symbol: str          # 600010.SH
    name: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    pct_chg: float
    volume_lot: float    # 成交量（手）
    amount_wan: float    # 成交额（万元）
    turnover: float      # 换手率
    volume_ratio: float  # 量比

    @property
    def change(self) -> float:
        return self.price - self.prev_close


class MarketRealtimeSnapshotService:
    """全市场盘中实时快照（后台维护 + 原子切换 + 新鲜度分级）

    进程内单例：同一进程所有调用共享同一快照和后台刷新器。
    """

    _instance: Optional["MarketRealtimeSnapshotService"] = None

    def __new__(cls, db_path: Path | None = None) -> "MarketRealtimeSnapshotService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init(db_path)
        return cls._instance

    def _init(self, db_path: Path | None = None) -> None:
        self._snapshot: list[RealtimeQuote] = []
        self._snapshot_time: float = 0.0
        self._snapshot_id: str = ""
        self._refresh_lock = threading.Lock()   # 防重复刷新
        self._data_lock = threading.Lock()      # 原子切换保护
        self._background_stop = threading.Event()
        self._background_thread: Optional[threading.Thread] = None
        # 调度统计（类型1：固定周期调度）
        self._refresh_started_at: float = 0.0
        self._refresh_finished_at: float = 0.0
        self._refresh_duration_ms: int = 0
        self._last_successful_snapshot_at: float = 0.0
        self._next_scheduled_at: float = 0.0
        self._skipped_due_to_lock: int = 0      # 被锁跳过的次数
        self._consecutive_failures: int = 0
        self._last_snapshot_interval_seconds: float = 0.0   # 实际完整快照间隔
        self._last_refresh_ok: bool = False
        if db_path is None:
            db_path = Path(__file__).resolve().parents[2] / "database" / "hermes_opc.duckdb"
        self.db_path = db_path

    # ------------------------------------------------------------------
    # 查询接口（只读，网络0）
    # ------------------------------------------------------------------
    def get_snapshot(self) -> tuple[list[RealtimeQuote], dict]:
        """返回 (快照数据, 元信息)。查询阶段无网络、无锁等待拉取。

        新鲜度：≤90s FRESH / 90-180s DEGRADED / >180s STALE_REALTIME
        STALE 时触发后台刷新（不阻塞查询）。
        """
        with self._data_lock:
            quotes = list(self._snapshot)
            snap_time = self._snapshot_time
            snap_id = self._snapshot_id

        age = (time.time() - snap_time) if snap_time else float("inf")
        if age <= FRESH_MAX_AGE:
            status = "FRESH"
        elif age <= DEGRADED_MAX_AGE:
            status = "DEGRADED"
        else:
            status = "STALE_REALTIME"
            # 触发后台刷新（非阻塞）
            self.ensure_background_refresh()

        meta = {
            "snapshot_id": snap_id,
            "snapshot_time": datetime.fromtimestamp(snap_time, tz=TZ).isoformat() if snap_time else "",
            "age_seconds": round(age, 1),
            "status": status,
            "quote_count": len(quotes),
            "realtime_data_cutoff": datetime.fromtimestamp(snap_time, tz=TZ).isoformat() if snap_time else "",
            # 调度统计
            "refresh_started_at": datetime.fromtimestamp(self._refresh_started_at, tz=TZ).isoformat() if self._refresh_started_at else "",
            "refresh_finished_at": datetime.fromtimestamp(self._refresh_finished_at, tz=TZ).isoformat() if self._refresh_finished_at else "",
            "refresh_duration_ms": self._refresh_duration_ms,
            "last_successful_snapshot_at": datetime.fromtimestamp(self._last_successful_snapshot_at, tz=TZ).isoformat() if self._last_successful_snapshot_at else "",
            "next_scheduled_at": datetime.fromtimestamp(self._next_scheduled_at, tz=TZ).isoformat() if self._next_scheduled_at else "",
            "skipped_due_to_lock": self._skipped_due_to_lock,
            "consecutive_failures": self._consecutive_failures,
            "actual_snapshot_interval_seconds": round(self._last_snapshot_interval_seconds, 1) if self._last_snapshot_interval_seconds else 0.0,
        }
        return quotes, meta

    def freshness_status(self) -> dict:
        """新鲜度状态（供双时间轴输出）"""
        _, meta = self.get_snapshot()
        return meta

    # ------------------------------------------------------------------
    # 后台刷新器（任务锁防重复 + 原子切换）
    # ------------------------------------------------------------------
    def ensure_background_refresh(self) -> None:
        """确保后台刷新线程在运行（幂等）"""
        with self._refresh_lock:
            if self._background_thread is not None and self._background_thread.is_alive():
                return  # 已有刷新器在跑（防重复）
            self._background_thread = threading.Thread(
                target=self._background_loop, daemon=True, name="realtime-snapshot-refresher"
            )
            self._background_thread.start()

    def _background_loop(self) -> None:
        """后台循环：完成后等待调度（类型2）

        每次刷新完成后等待 REFRESH_INTERVAL_SECONDS（60秒）再触发下一次。
        实际完整快照间隔 = 刷新耗时(66s) + 等待(60s) ≈ 126秒（实测确认）。
        若某次刷新超过周期，任务锁保证不并发启动（后续触发被跳过计数）。
        """
        while not self._background_stop.is_set():
            # 记录下一次计划触发时间
            self._next_scheduled_at = time.time() + REFRESH_INTERVAL_SECONDS
            ok = self.refresh_once()
            if not ok and not self._last_refresh_ok:
                # 被锁跳过（非失败）不计入 consecutive_failures
                if not self._refresh_lock.locked():
                    pass
            self._background_stop.wait(REFRESH_INTERVAL_SECONDS)

    def refresh_once(self) -> bool:
        """单次刷新（原子切换）。成功返回 True。

        - 任务锁：防并发重复拉取（运行中 → 跳过并计数）
        - 原子切换：完全拉取成功后整体替换
        """
        # 记录计划触发时间
        self._next_scheduled_at = time.time() + REFRESH_INTERVAL_SECONDS
        if not self._refresh_lock.acquire(blocking=False):
            self._skipped_due_to_lock += 1   # 被锁跳过计数
            return False  # 另一个刷新正在进行

        self._refresh_started_at = time.time()
        try:
            quotes = self._fetch_from_tencent()
            self._refresh_finished_at = time.time()
            self._refresh_duration_ms = int(
                (self._refresh_finished_at - self._refresh_started_at) * 1000)
            if not quotes:
                self._consecutive_failures += 1
                self._last_refresh_ok = False
                return False

            # 计算实际快照间隔（与上一次成功切换时间）
            if self._last_successful_snapshot_at > 0:
                self._last_snapshot_interval_seconds = (
                    self._refresh_finished_at - self._last_successful_snapshot_at)
            # 原子切换：完全拉取成功后整体替换
            with self._data_lock:
                self._snapshot = quotes
                self._snapshot_time = self._refresh_finished_at
                self._snapshot_id = f"rt_{int(self._refresh_finished_at)}"
            self._last_successful_snapshot_at = self._refresh_finished_at
            self._consecutive_failures = 0
            self._last_refresh_ok = True
            return True
        except Exception:
            self._refresh_finished_at = time.time()
            self._refresh_duration_ms = int(
                (self._refresh_finished_at - self._refresh_started_at) * 1000)
            self._consecutive_failures += 1
            self._last_refresh_ok = False
            return False
        finally:
            self._refresh_lock.release()

    def stop_background(self) -> None:
        """停止后台刷新器（测试用）"""
        self._background_stop.set()
        if self._background_thread:
            self._background_thread.join(timeout=5)

    # ------------------------------------------------------------------
    # 旧接口兼容（同步拉取，仅测试/首次使用）
    # ------------------------------------------------------------------
    def fetch_all(self, force: bool = False) -> list[RealtimeQuote]:
        """同步拉取（保留兼容）。正常查询应走 get_snapshot()。"""
        if not force and self._snapshot:
            quotes, meta = self.get_snapshot()
            if meta["status"] != "STALE_REALTIME":
                return quotes
        self.refresh_once()
        with self._data_lock:
            return list(self._snapshot)

    def _symbols(self) -> list[str]:
        """从股票池读代码（转腾讯格式）"""
        import duckdb
        con = open_database(str(self.db_path), read_only=True)
        rows = con.execute(
            "SELECT symbol FROM stock_universe WHERE listing_status='ACTIVE'"
        ).fetchall()
        con.close()
        out = []
        for (s,) in rows:
            code = s.split(".")[0]
            out.append(("sh" if s.endswith(".SH") else "sz") + code)
        return out

    def _fetch_from_tencent(self) -> list[RealtimeQuote]:
        symbols = self._symbols()
        batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(pool.map(self._fetch_batch, batches))

        quotes: list[RealtimeQuote] = []
        for batch_lines in results:
            for q in batch_lines:
                if q is not None:
                    quotes.append(q)
        return quotes

    def _fetch_batch(self, codes: list[str]) -> list[Optional[RealtimeQuote]]:
        url = "https://qt.gtimg.cn/q=" + ",".join(codes)
        try:
            req = urllib.request.Request(url, headers=TENCENT_HEADERS)
            raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", errors="ignore")
        except Exception:
            return []
        out = []
        for line in raw.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            try:
                fields = line.split("=", 1)[1].strip().split("~")
                if len(fields) < 40:
                    continue
                quote = RealtimeQuote(
                    symbol=_to_symbol(fields[2], fields[0]),
                    name=fields[1],
                    price=_f(fields[3]),
                    prev_close=_f(fields[4]),
                    open=_f(fields[5]),
                    high=_f(fields[33]),
                    low=_f(fields[34]),
                    pct_chg=_f(fields[32]),
                    volume_lot=_f(fields[6]),
                    amount_wan=_f(fields[37]),
                    turnover=_f(fields[38]),
                    volume_ratio=_f(fields[49]) if len(fields) > 49 else 0,
                )
                out.append(quote)
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------
    # 筛选接口（今日/盘中语义）
    # ------------------------------------------------------------------
    def top_gainers(self, n: int = 20, min_amount_wan: float = 5000.0) -> list[RealtimeQuote]:
        """今日涨幅榜（过滤成交额过小）"""
        quotes, _ = self.get_snapshot()
        return sorted(
            [q for q in quotes if q.amount_wan >= min_amount_wan],
            key=lambda q: -q.pct_chg,
        )[:n]

    def volume_spike(self, n: int = 20, min_ratio: float = 2.0,
                     min_amount_wan: float = 5000.0) -> list[RealtimeQuote]:
        """今日放量异动（量比高 + 上涨）"""
        quotes, _ = self.get_snapshot()
        return sorted(
            [q for q in quotes if q.volume_ratio >= min_ratio
             and q.pct_chg > 0 and q.amount_wan >= min_amount_wan],
            key=lambda q: -q.volume_ratio,
        )[:n]

    def top_turnover(self, n: int = 20, min_amount_wan: float = 5000.0) -> list[RealtimeQuote]:
        """高换手活跃股"""
        quotes, _ = self.get_snapshot()
        return sorted(
            [q for q in quotes if q.amount_wan >= min_amount_wan],
            key=lambda q: -q.turnover,
        )[:n]


def _f(s: str) -> float:
    try:
        return float(s) if s else 0.0
    except (ValueError, TypeError):
        return 0.0


def _to_symbol(code: str, market_prefix: str) -> str:
    """腾讯代码 → 标准符号（600010→600010.SH）"""
    try:
        num = int(code)
    except (ValueError, TypeError):
        return code
    if num >= 600000 or num < 1000:
        suffix = ".SH"
    else:
        suffix = ".SZ"
    return f"{code}{suffix}"
