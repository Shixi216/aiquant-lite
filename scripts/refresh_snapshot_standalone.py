"""独立进程：全市场快照批量刷新（规则6）

由 scanner 守卫或 cron 触发，独立进程运行，父进程退出不影响。
网络慢时约需 30-40 分钟完成全市场 5534 只。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TZ = timezone(timedelta(hours=8))


def main() -> None:
    start = time.perf_counter()
    from trading.scanner.refresh_task_lock import RefreshTaskLock

    # 任务锁：已有运行中任务则不重复启动
    lock = RefreshTaskLock()
    task_id = lock.acquire()
    if task_id is None:
        print(json.dumps({
            "时间": datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "跳过": True,
            "原因": "已有刷新任务运行中，不重复启动（任务锁）",
        }, ensure_ascii=False))
        return

    try:
        from trading.scanner.freshness_guard import ScannerFreshnessGuard

        guard = ScannerFreshnessGuard()
        db_path = Path("database/hermes_opc.duckdb")

        # 用临时结果对象执行刷新
        from trading.scanner.freshness_guard import FreshnessCheckResult
        result = FreshnessCheckResult(
            fresh=False,
            data_cutoff=datetime.now(tz=TZ),
            latest_trade_date=guard._latest_trade_date() or "",
        )
        # 心跳更新
        lock.heartbeat(task_id, progress="拉取全市场快照中")
        refreshed = guard._do_refresh_inner(result, db_path)
        lock.heartbeat(task_id, progress="完成",
                       success_count=1 if refreshed.fresh else 0,
                       coverage_ratio=refreshed.coverage_ratio if hasattr(refreshed, "coverage_ratio") else 0,
                       last_snapshot_id=refreshed.snapshot_id or "")

        # 记录日志
        log = Path("logs/snapshot_refresh.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "时间": datetime.now(tz=TZ).isoformat(),
                "任务ID": task_id,
                "新鲜": refreshed.fresh,
                "原因": refreshed.reason,
                "快照ID": refreshed.snapshot_id,
                "最近交易日": refreshed.latest_trade_date,
                "耗时秒": round(time.perf_counter() - start, 1),
            }, ensure_ascii=False) + "\n")

        print(json.dumps({
            "任务ID": task_id,
            "新鲜": refreshed.fresh,
            "原因": refreshed.reason,
            "耗时秒": round(time.perf_counter() - start, 1),
        }, ensure_ascii=False))
    finally:
        lock.release(task_id)


if __name__ == "__main__":
    main()
