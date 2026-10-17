# ruff: noqa: E402
# Standalone CLI bootstrap configures paths or runtime before project imports.
"""全市场财务数据补全：ACTIVE 股票三表 -> canonical_financial_records

只写 canonical_financial_records（经 FinancialStatementService 标准链路），
不触碰任何现有业务代码。支持断点续跑：
  scripts/_backfill_financial_state.json   已完成/已失败记录
  logs/backfill_financial_failures.log     失败明细

用法：
  export HERMES_DB_OWNER_PROCESS=router
  export HERMES_DB_ENFORCE_OWNER=1
  .venv/Scripts/python.exe scripts/backfill_financial_data.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 必须在导入任何 database 模块之前声明主库写身份，
# 否则 get_connection 会被 DatabaseOwnershipError 拦截。
os.environ.setdefault("HERMES_DB_OWNER_PROCESS", "router")
os.environ.setdefault("HERMES_DB_ENFORCE_OWNER", "1")

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config.network import configure_network_policy  # noqa: E402

START_DATE = "20230101"
# 2026-09-29 第二批L11修复：END_DATE 改动态当日（原硬编码 20260831，
# 时间推移后会静默漏补最新报告期）。
from datetime import date as _date
END_DATE = _date.today().strftime("%Y%m%d")
SLEEP_SECONDS = 0.4
RETRIES = 3
RETRY_BACKOFF = (3.0, 10.0)
STATE_PATH = ROOT / "scripts" / "_backfill_financial_state.json"
FAIL_LOG_PATH = ROOT / "logs" / "backfill_financial_failures.log"


def normalize_symbol(value: str) -> str:
    normalized = str(value).strip().upper()
    code, _, exchange = normalized.partition(".")
    if exchange not in {"SH", "SZ", "BJ"}:
        # 2026-09-29 第二批M1修复：北交所 920 段（如 920001）开头是9，
        # 原被 startswith("9") 误归上海 → .SH，后续 Tushare 拉数全空。
        # 判定顺序：92 开头 → BJ，然后才轮到沪市 5/6/9。
        if code.startswith("92"):
            exchange = "BJ"
        elif code.startswith(("5", "6", "9")):
            exchange = "SH"
        elif code.startswith(("4", "8")):
            exchange = "BJ"
        else:
            exchange = "SZ"
    return f"{code}.{exchange}"


def load_done_symbols(min_end_date: str | None = None) -> set[str]:
    """已补齐的 symbol 集合。

    2026-09-29 第二批M2修复：原版只查 DISTINCT symbol，任一历史记录
    就整股跳过 → 新报告期（季报）发布后永远不会补。现在按
    "该股最新 end_date >= min_end_date" 判定，未达最新报告期的
    股票会重新进入待补队列。
    """
    from database.db import open_database

    with open_database(
        ROOT / "database" / "hermes_opc.duckdb",
        read_only=True,
    ) as connection:
        if min_end_date:
            rows = connection.execute(
                """
                SELECT symbol, MAX(report_period) AS latest
                FROM canonical_financial_records
                GROUP BY symbol
                HAVING MAX(report_period) >= ?
                """,
                [min_end_date],
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT DISTINCT symbol FROM canonical_financial_records"
            ).fetchall()
    return {normalize_symbol(row[0]) for row in rows}


def load_active_symbols() -> list[str]:
    from database.db import open_database

    with open_database(
        ROOT / "database" / "hermes_opc.duckdb",
        read_only=True,
    ) as connection:
        rows = connection.execute(
            "SELECT DISTINCT symbol FROM stock_universe "
            "WHERE listing_status = 'ACTIVE' ORDER BY symbol"
        ).fetchall()
    return [normalize_symbol(row[0]) for row in rows]


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return {
                "done": set(state.get("done", [])),
                "failed": dict(state.get("failed", {})),
                # 2026-09-29 M2补丁：state 记录的基线报告期。done 集合只在
                # 同一基线内有效——新报告期披露后 done 必须整体失效重判。
                "baseline": state.get("baseline"),
            }
        except (json.JSONDecodeError, OSError):
            pass
    return {"done": set(), "failed": {}, "baseline": None}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(
            {
                "done": sorted(state["done"]),
                "failed": state["failed"],
                "baseline": state.get("baseline"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def log_failure(symbol: str, error: str) -> None:
    FAIL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with FAIL_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp}\t{symbol}\t{error}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="补全全市场 ACTIVE 股票财务数据"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="仅处理前 N 只（冒烟测试用）",
    )
    args = parser.parse_args()

    configure_network_policy()

    print("[1/4] 读取 canonical_financial_records 已有 symbol ...")
    # 2026-09-29 第二批M2修复：以"最新报告期"为补数基线，原版只查
    # DISTINCT symbol，任一历史记录就整股跳过 → 新报告期永远不会补。
    # 应补的最新报告期下界（4/30年报、8/31中报、10/31三季报披露截止）：
    # 过8/31基线=今年中报，过10/31基线=今年三季报，否则上一年年报。
    from datetime import date as _d
    today = _d.today()
    min_end = f"{today.year - 1}1231"
    if (today.month, today.day) >= (10, 31):
        min_end = f"{today.year}0930"
    elif (today.month, today.day) >= (8, 31):
        min_end = f"{today.year}0630"
    done_db = load_done_symbols(min_end_date=min_end)
    print(f"      已达最新报告期(≥{min_end}): {len(done_db)} 只")

    print("[2/4] 读取 stock_universe ACTIVE 列表 ...")
    active = load_active_symbols()
    print(f"      ACTIVE 股票: {len(active)} 只")

    state = load_state()
    # 2026-09-29 M2补丁：state 基线过期（新报告期披露季）→ done 整体失效，
    # 只保留 failed（避免反复重撞已知失败票）。否则 state 里 5534 只
    # "历史成功" 会把待补队列挡成 0。
    if state.get("baseline") != min_end:
        if state.get("baseline") is not None:
            print(
                f"      报告期基线变化 {state.get('baseline')} → {min_end}，"
                f"重置历史成功 {len(state['done'])} 只"
            )
        state["done"] = set()
        state["baseline"] = min_end
    state["done"].update(done_db)

    pending = [
        symbol
        for symbol in active
        if symbol not in state["done"]
        and symbol not in state["failed"]
    ]
    if args.limit is not None:
        pending = pending[: args.limit]

    total = len(pending)
    print(
        f"[3/4] 待补 {total} 只"
        f"（跳过已在库 {len(done_db)} 只、"
        f"历史成功 {max(len(state['done']) - len(done_db), 0)} 只、"
        f"历史失败 {len(state['failed'])} 只）"
    )
    if total == 0:
        print("无待补股票，退出")
        save_state(state)
        return

    from data_hub.services.financial_statement_service import (
        FinancialStatementService,
    )

    service = FinancialStatementService()
    start = time.time()
    succeeded_now = 0
    failed_now = 0

    for index, symbol in enumerate(pending, 1):
        last_error = ""
        for attempt in range(RETRIES):
            try:
                service.get_financial_statement(
                    symbol=symbol,
                    start_date=START_DATE,
                    end_date=END_DATE,
                    persist=True,
                )
                state["done"].add(symbol)
                state["failed"].pop(symbol, None)
                succeeded_now += 1
                break
            except Exception as exc:  # noqa: BLE001 单只失败不中断
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < RETRIES - 1:
                    time.sleep(RETRY_BACKOFF[min(attempt, 1)])
        else:
            state["failed"][symbol] = last_error
            failed_now += 1
            log_failure(symbol, last_error)

        if index % 100 == 0 or index == total:
            elapsed = time.time() - start
            rate = index / elapsed if elapsed > 0 else 0.0
            remaining = (total - index) / rate if rate > 0 else 0.0
            print(
                f"进度 {index}/{total} "
                f"成功={succeeded_now} 失败={failed_now} "
                f"累计失败={len(state['failed'])} "
                f"用时={elapsed / 60:.1f}分钟 预计剩余={remaining / 60:.1f}分钟",
                flush=True,
            )
            save_state(state)

        time.sleep(SLEEP_SECONDS)

    save_state(state)
    elapsed = time.time() - start
    print(
        f"[4/4] 完成：本轮成功 {succeeded_now} 只、失败 {failed_now} 只，"
        f"总用时 {elapsed / 60:.1f} 分钟"
    )
    print(
        f"      状态：成功 {len(state['done'])} 只、"
        f"失败 {len(state['failed'])} 只"
    )
    failures = list(state["failed"].items())
    if failures:
        preview = "; ".join(
            f"{symbol}: {message}" for symbol, message in failures[:10]
        )
        print(f"      失败样例: {preview}")


if __name__ == "__main__":
    main()
