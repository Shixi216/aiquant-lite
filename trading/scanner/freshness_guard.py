"""Scanner 数据新鲜度守卫（6条硬性规则）

规则：
1. 扫描前强制检查数据新鲜度
2. 快照过期时自动批量刷新（不逐股请求）
3. 同一任务固定一个 snapshot_id 和 data_cutoff
4. 禁止扫描用旧价格、研究阶段再偷偷补新价格（point-in-time 一致性）
5. 刷新失败时停止推荐，只返回"数据陈旧"
6. 桌面端和 Hermes Gateway 启动后自动运行定时刷新任务（由外部 cron 触发本守卫的 refresh）
"""
from __future__ import annotations

from database.db import open_database

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

TZ = timezone(timedelta(hours=8))


@dataclass
class FreshnessCheckResult:
    fresh: bool                       # 是否最新
    snapshot_id: str | None = None    # 固定快照ID（规则3）
    snapshot_time: datetime | None = None
    data_cutoff: datetime | None = None
    latest_trade_date: str = ""       # 最近交易日
    reason: str = ""                  # 过期原因（中文）
    auto_refreshed: bool = False      # 是否自动批量刷新
    refresh_failed: bool = False      # 刷新失败（规则5）
    refresh_error: str = ""


class ScannerFreshnessGuard:
    """扫描前新鲜度检查 + 快照批量刷新"""

    def __init__(self, repository=None):
        self.repository = repository

    # ------------------------------------------------------------------
    # 规则1：获取最近交易日（Tushare 交易日历，注意返回降序）
    # ------------------------------------------------------------------
    def _latest_trade_date(self) -> str | None:
        """最近交易日（统一 TradingCalendarService，不依赖返回顺序）"""
        try:
            from data_hub.services.trading_calendar_service import TradingCalendarService
            return TradingCalendarService().latest_trade_date(as_str=True)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 规则1+3：检查快照新鲜度（传入 data_cutoff 固定时间点）
    # ------------------------------------------------------------------
    def check(
        self,
        data_cutoff: datetime | None = None,
        *,
        force_refresh: bool = False,
        max_age_hours: float = 12.0,
    ) -> FreshnessCheckResult:
        """扫描前强制检查。过期时自动批量刷新；刷新失败返回数据陈旧。"""
        import duckdb
        from config.settings import settings

        db_path = Path(settings.opc_database_path)
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parents[2] / db_path

        result = FreshnessCheckResult(
            fresh=False,
            data_cutoff=data_cutoff or datetime.now(tz=TZ),
        )

        # 最近交易日
        latest_trade = self._latest_trade_date()
        if latest_trade:
            result.latest_trade_date = latest_trade
        else:
            result.reason = "无法获取交易日历"
            return result

        # 查询最新快照
        try:
            con = open_database(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT snapshot_id, snapshot_time, data_cutoff FROM market_snapshot_runs "
                "ORDER BY snapshot_time DESC LIMIT 1"
            ).fetchall()
            con.close()
        except Exception:
            rows = []

        # 无快照 → 必须刷新
        if not rows:
            result.reason = "无快照数据"
            if force_refresh or True:  # 扫描前强制要求最新
                return self._refresh_and_recheck(result, db_path, force_refresh)
            return result

        snapshot_id, snapshot_time, snapshot_cutoff = rows[0]
        result.snapshot_id = snapshot_id
        result.snapshot_time = snapshot_time

        # 规则1+2：检查覆盖度（明细数 vs 股票池），不足则刷新
        try:
            con = open_database(str(db_path), read_only=True)
            item_count = con.execute(
                "SELECT COUNT(*) FROM market_snapshot_items WHERE snapshot_id=?",
                [snapshot_id],
            ).fetchone()[0]
            universe_count = con.execute(
                "SELECT COUNT(*) FROM stock_universe WHERE listing_status='ACTIVE'"
            ).fetchone()[0]
            con.close()
        except Exception:
            item_count, universe_count = 0, 0

        # 覆盖不足（<90%股票池）→ 必须刷新（规则2）
        if universe_count > 0 and item_count < universe_count * 0.9:
            result.reason = f"快照覆盖不足（{item_count}/{universe_count}只）"
            return self._refresh_and_recheck(result, db_path, force_refresh)

        # 快照日期 < 最近交易日 → 过期
        snap_date = str(snapshot_time)[:10].replace("-", "")
        if snap_date < latest_trade:
            result.reason = f"快照过期（快照 {snap_date}，最近交易日 {latest_trade}）"
            # 规则2：自动批量刷新
            return self._refresh_and_recheck(result, db_path, force_refresh)

        # 快照数据截止 < 最近完成交易日 → 需要刷新
        # （用快照 data_cutoff 而非生成时间：盘中快照的 data_cutoff 是最近完成日，
        #   即使生成超时也不陈旧——没有更新的数据可用）
        cutoff_str = str(snapshot_cutoff)[:10].replace("-", "") if snapshot_cutoff else ""
        if cutoff_str and cutoff_str < latest_trade:
            result.reason = f"快照数据截止过期（{cutoff_str}，最近完成交易日 {latest_trade}）"
            return self._refresh_and_recheck(result, db_path, force_refresh)

        # 快照生成时间过旧（超过 max_age_hours）且最近完成交易日已有更新数据
        # → 仅在"存在更新的收盘数据"时触发（非盘中误判）
        now = datetime.now(tz=TZ)
        if snapshot_time and (now - snapshot_time).total_seconds() > max_age_hours * 3600:
            # 生成超时但数据截止仍是最新完成日 → 数据本身有效（如周末/假日）
            # 仅当最近完成交易日 > 快照 data_cutoff 时才算真过期（上面已处理）
            result.fresh = True
            result.reason = "数据最新（快照生成较早但数据截止为最近完成交易日）"
            return result

        result.fresh = True
        result.reason = "数据最新"
        return result

    # ------------------------------------------------------------------
    # 规则2+5：批量刷新快照（一次全市场拉取，不逐股请求）
    # ------------------------------------------------------------------
    def _refresh_and_recheck(
        self, result: FreshnessCheckResult, db_path: Path, force_refresh: bool
    ) -> FreshnessCheckResult:
        """刷新策略：触发独立进程后台刷新，本次返回数据陈旧（不阻塞扫描）。

        规则5：刷新失败/未完成 → 停止推荐，只返回"数据陈旧"。
        规则6：刷新由独立进程执行（不依赖父进程存活），完成后下次检查通过。
        """
        import subprocess
        import sys

        # 独立进程执行刷新脚本（detached，父进程退出不影响）
        script = Path(__file__).resolve().parents[2] / "scripts" / "refresh_snapshot_standalone.py"
        try:
            python = sys.executable
            subprocess.Popen(
                [python, str(script)],
                cwd=str(Path(__file__).resolve().parents[2]),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            result.refresh_error = f"启动刷新进程失败: {str(exc)[:60]}"

        # 本次：停止推荐，返回数据陈旧（规则5）
        result.fresh = False
        result.refresh_failed = False  # 后台进行中，不算失败
        result.reason = "数据陈旧（已触发后台批量刷新，请稍后重试）"
        return result

    def _do_refresh_inner(
        self, result: FreshnessCheckResult, db_path: Path
    ) -> FreshnessCheckResult:
        try:
            # 规则2+新规则：Tushare 为主源批量刷新（4接口×1请求/接口）
            # 腾讯/AKShare 仅失败备用，BaoStock 仅历史校验
            from data_hub.providers.tushare_snapshot import TushareSnapshotProvider
            provider = TushareSnapshotProvider()

            snap = provider.fetch_snapshot()
            if snap.error and snap.daily.empty:
                # Tushare 主源失败 → 备用：腾讯源
                return self._fallback_tencent(result, db_path)

            # 写入快照（Tushare 数据）
            self._write_tushare_snapshot(db_path, snap)
            result.auto_refreshed = True

            # 刷新后重新读取快照
            import duckdb
            con = open_database(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT snapshot_id, snapshot_time FROM market_snapshot_runs "
                "ORDER BY snapshot_time DESC LIMIT 1"
            ).fetchall()
            con.close()
            if rows:
                result.snapshot_id = rows[0][0]
                result.snapshot_time = rows[0][1]
                snap_date = str(rows[0][1])[:10].replace("-", "")
                if snap_date >= result.latest_trade_date:
                    result.fresh = True
                    result.reason = (
                        f"批量刷新成功（快照 {snap_date}，Tushare主源，"
                        f"覆盖{snap.coverage_ratio:.0%}，moneyflow{'缺失' if snap.moneyflow_missing else '完整'}）"
                    )
                else:
                    result.reason = f"刷新后仍过期（快照 {snap_date}，需 {result.latest_trade_date}）"
                    result.fresh = False
            else:
                result.reason = "刷新后仍无快照"
                result.fresh = False
            return result
        except Exception as exc:
            # 规则5：刷新失败 → 停止推荐，只返回"数据陈旧"
            result.fresh = False
            result.refresh_failed = True
            result.refresh_error = f"{type(exc).__name__}: {str(exc)[:80]}"
            result.reason = "数据陈旧（刷新失败）"
            return result

    def _fallback_tencent(self, result: FreshnessCheckResult, db_path: Path) -> FreshnessCheckResult:
        """备用源：腾讯全市场快照（Tushare 失败时）"""
        try:
            from data_hub.providers.tencent_snapshot import TencentSnapshotProvider
            import duckdb

            provider = TencentSnapshotProvider()
            con = open_database(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT symbol FROM stock_universe WHERE listing_status='ACTIVE' LIMIT 10000"
            ).fetchall()
            con.close()
            symbols = [r[0].split(".")[0] for r in rows if r[0]]
            frame = provider.fetch_market_snapshot(symbols)
            if frame is None or frame.empty:
                raise RuntimeError("腾讯备用源返回空")
            self._write_snapshot(db_path, frame, "Tencent")
            result.auto_refreshed = True
            result.fresh = True
            result.reason = "批量刷新成功（备用源：腾讯，Tushare主源失败）"
            return result
        except Exception as exc:
            result.fresh = False
            result.refresh_failed = True
            result.refresh_error = f"备用源失败: {str(exc)[:80]}"
            result.reason = "数据陈旧（主源+备用均失败）"
            return result

    def _write_tushare_snapshot(self, db_path: Path, snap) -> None:
        """把 Tushare 四接口数据写入快照表"""
        import uuid
        import duckdb
        import json

        snapshot_id = f"mkt_{uuid.uuid4().hex[:20]}"
        now = datetime.now(tz=TZ)
        trade_date = snap.trade_date  # 实际交易日期

        con = open_database(str(db_path))
        try:
            # 快照运行记录（trade_date 体现在 snapshot_time 中，另存 payload）
            con.execute(
                """
                INSERT INTO market_snapshot_runs
                (snapshot_id, mode, provider, data_cutoff, expected_universe_size,
                 received_symbol_count, valid_symbol_count, missing_symbol_count,
                 coverage_ratio, completeness_status, started_at, completed_at,
                 snapshot_time, content_hash, request_count, elapsed_seconds,
                 peak_memory_bytes, database_growth_bytes, error_message)
                VALUES (?, 'APPLY', 'Tushare', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot_id, now,
                    len(snap.daily), len(snap.daily), len(snap.daily), 0,
                    snap.coverage_ratio,
                    "COMPLETE" if snap.coverage_ratio >= 0.99 else "PARTIAL",
                    now, now, now,
                    f"tushare_{trade_date}_{int(now.timestamp())}",
                    snap.request_count, 0.0, 0, 0,
                    snap.error or None,
                ],
            )

            # 明细：合并 daily + daily_basic + moneyflow + stk_limit（按 ts_code 对齐）
            basic_map = {}
            if not snap.daily_basic.empty:
                basic_map = {
                    r["ts_code"]: r
                    for _, r in snap.daily_basic.iterrows()
                }
            mf_map = {}
            if not snap.moneyflow.empty:
                mf_map = {r["ts_code"]: r for _, r in snap.moneyflow.iterrows()}
            limit_map = {}
            if not snap.stk_limit.empty:
                limit_map = {r["ts_code"]: r for _, r in snap.stk_limit.iterrows()}

            rows = []
            for _, r in snap.daily.iterrows():
                code = r["ts_code"]
                b = basic_map.get(code)
                m = mf_map.get(code)
                l = limit_map.get(code)
                payload = {
                    "trade_date": trade_date,
                    "snapshot_time": now.isoformat(),
                    "collected_at": now.isoformat(),
                    "source": "Tushare",
                    "open": r.get("open"), "high": r.get("high"),
                    "low": r.get("low"), "close": r.get("close"),
                    "pct_chg": r.get("pct_chg"), "volume": r.get("vol"),
                    "amount": r.get("amount"),
                    "turnover_rate": b.get("turnover_rate") if b is not None else None,
                    "pe_ttm": b.get("pe_ttm") if b is not None else None,
                    "pb": b.get("pb") if b is not None else None,
                    "total_mv": b.get("total_mv") if b is not None else None,
                    "moneyflow_net": (m.get("net_mf_amount") if m is not None else None),
                    "limit_up": (l.get("up_limit") if l is not None else None),
                    "limit_down": (l.get("down_limit") if l is not None else None),
                }
                rows.append((
                    snapshot_id, code,
                    float(r["close"]),                      # price
                    float(r["pre_close"]) if r.get("pre_close") is not None else None,
                    float(r["open"]) if r.get("open") is not None else None,
                    float(r["high"]) if r.get("high") is not None else None,
                    float(r["low"]) if r.get("low") is not None else None,
                    float(r.get("vol") or 0),               # volume(手)
                    float(r.get("amount") or 0),            # amount(千元)
                    float(r.get("change") or 0),
                    float(r.get("pct_chg") or 0),           # change_pct
                    float(b["turnover_rate"]) if b is not None and b.get("turnover_rate") is not None else None,
                    now,                                    # snapshot_time
                    "Tushare",                              # source
                    "OK", False, False, None,
                    json.dumps(payload, ensure_ascii=False),  # payload_json
                ))
            con.executemany(
                """
                INSERT OR REPLACE INTO market_snapshot_items
                (snapshot_id, symbol, price, previous_close, open, high, low,
                 volume, amount, change, change_pct, turnover_rate, snapshot_time,
                 source, item_status, is_suspended, is_abnormal, raw_record_id, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        finally:
            con.close()

    def _write_snapshot(self, db_path: Path, frame, provider_name: str) -> None:
        """把腾讯快照数据写入 market_snapshot_runs / market_snapshot_items（匹配真实表结构）"""
        import uuid
        import duckdb

        snapshot_id = f"mkt_{uuid.uuid4().hex[:20]}"
        now = datetime.now(tz=TZ)

        con = open_database(str(db_path))
        try:
            # 快照运行记录（market_snapshot_runs 表结构）
            con.execute(
                """
                INSERT INTO market_snapshot_runs
                (snapshot_id, mode, provider, data_cutoff, expected_universe_size,
                 received_symbol_count, valid_symbol_count, missing_symbol_count,
                 coverage_ratio, completeness_status, started_at, completed_at,
                 snapshot_time, content_hash, request_count, elapsed_seconds,
                 peak_memory_bytes, database_growth_bytes, error_message)
                VALUES (?, 'APPLY', ?, ?, ?, ?, ?, ?, ?, 'COMPLETED', ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                [
                    snapshot_id, provider_name, now, len(frame),
                    len(frame), len(frame), 0,
                    1.0, now, now, now,
                    f"tencent_{int(now.timestamp())}",
                    max(1, len(frame) // 80), 0.0, 0, 0,
                ],
            )
            # 快照明细（market_snapshot_items 表结构）
            rows = []
            for _, r in frame.iterrows():
                payload = {
                    "symbol": str(r["symbol"]),
                    "price": float(r["close"]) if r["close"] is not None else None,
                    "change_pct": float(r["pct_chg"]) if r["pct_chg"] is not None else None,
                    "volume": float(r.get("volume") or 0),
                    "amount": float(r.get("amount") or 0),
                    "source": provider_name,
                }
                rows.append((
                    snapshot_id,
                    str(r["symbol"]),
                    float(r["close"]) if r["close"] is not None else None,          # price
                    None,  # previous_close
                    float(r.get("open")) if r.get("open") is not None else None,    # open
                    float(r.get("high")) if r.get("high") is not None else None,    # high
                    float(r.get("low")) if r.get("low") is not None else None,      # low
                    float(r.get("volume") or 0),                                     # volume
                    float(r.get("amount") or 0),                                     # amount
                    None,  # change
                    float(r["pct_chg"]) if r["pct_chg"] is not None else None,      # change_pct
                    float(r["turnover_rate"]) if r.get("turnover_rate") is not None else None,
                    now,  # snapshot_time
                    provider_name,  # source
                    "OK",  # item_status
                    False,  # is_suspended
                    False,  # is_abnormal
                    None,  # raw_record_id
                    json.dumps(payload, ensure_ascii=False),  # payload_json
                ))
            con.executemany(
                """
                INSERT OR REPLACE INTO market_snapshot_items
                (snapshot_id, symbol, price, previous_close, open, high, low,
                 volume, amount, change, change_pct, turnover_rate, snapshot_time,
                 source, item_status, is_suspended, is_abnormal, raw_record_id, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        finally:
            con.close()
