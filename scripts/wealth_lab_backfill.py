"""Wealth Lab 数据池回填：双源拉取四只 ETF 日线+复权因子写入自建库。

官方源优先，积分/限频/网络失败自动降级代理源；行级 data_source 审计。

用法：
    ./.venv/Scripts/python.exe -m scripts.wealth_lab_backfill [--start 20200101] [--end 20261231]
"""
from __future__ import annotations

import argparse
import json

from trading.wealth_lab.config import ASSETS
from trading.wealth_lab.provider import DualSourceProvider, source_now
from trading.wealth_lab.repository import WealthLabRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 Wealth Lab ETF 数据池（双源）")
    parser.add_argument("--start", default="20200101")
    parser.add_argument("--end", default="20261231")
    args = parser.parse_args()

    dual = DualSourceProvider()
    if not (dual.official.ready or dual.proxy.ready):
        print(json.dumps({"status": "UNAVAILABLE", "reason": "官方与代理源均未配置"}, ensure_ascii=False))
        return

    repo = WealthLabRepository()
    summary: dict = {"started_at": source_now().isoformat(), "assets": {}}
    for symbol in ASSETS:
        frame, source_status = dual.fetch_fund_daily(symbol, args.start, args.end)
        written = repo.upsert_daily(frame, symbol)
        coverage = repo.asset_coverage(symbol)
        summary["assets"][symbol] = {
            "fetched_rows": int(len(frame)) if frame is not None else 0,
            "written_rows": written,
            "pool_rows": coverage["rows"],
            "pool_first": coverage["first"],
            "pool_last": coverage["last"],
            "source_status": source_status,
            "source_breakdown": coverage["source_breakdown"],
            "adj_missing": (
                int(len(frame) - frame["adj_factor"].notna().sum())
                if frame is not None and not frame.empty
                else 0
            ),
        }
    summary["pool"] = repo.pool_status()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
