# -*- coding: utf-8 -*-
"""v3 第三/四步：分批拉取候选池历史数据并做质量审计入库。

复用 DualSourceProvider（官方优先+降级）与 etf_daily 表；
资产主数据与质量审计写入 wealth_assets。
用法：PYTHONPATH=. ./.venv/Scripts/python.exe -m scripts.wealth_lab_v3_backfill
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone

from trading.wealth_lab.provider import DualSourceProvider, source_now
from trading.wealth_lab.repository import WealthLabRepository
from trading.wealth_lab.universe import upsert_assets

_TZ = timezone(timedelta(hours=8))
POOL_START = date(2020, 1, 2)

# 与 runtime/_v3_candidates.json 一致（真实名录核验通过）
CATEGORIES = {
    "CN_EQUITY_BROAD_ETF": ["510300.SH", "510500.SH", "159915.SZ", "588000.SH", "159949.SZ", "510880.SH"],
    "CN_INDUSTRY_ETF": ["512480.SH", "515030.SH", "512170.SH", "512690.SH", "512800.SH", "512660.SH"],
    "CN_STYLE_ETF": ["512890.SH", "563020.SH", "159545.SZ"],
    "CN_BOND_ETF": ["511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ"],
    "CN_COMMODITY_ETF": ["518880.SH", "159985.SZ", "162411.SZ"],
    "CN_CONVERTIBLE_ETF": ["511380.SH"],
    "QDII_ETF": ["513100.SH", "513500.SH", "513050.SH", "510900.SH", "513030.SH"],
    "CN_REIT": ["508056.SH", "180101.SZ", "508000.SH", "180201.SZ"],
}


def _expected_days(list_date: date | None, asof: date) -> int:
    start = max(list_date or POOL_START, POOL_START)
    if start >= asof:
        return 0
    # 交易日近似：自然日 × 5/7（仅作缺失率分母参考，标注为近似口径）
    return max(1, int((asof - start).days * 5 / 7))


def main() -> None:
    dual = DualSourceProvider()
    repo = WealthLabRepository()
    summary: dict = {"started_at": source_now().isoformat(), "assets": {}, "errors": {}}

    for category, symbols in CATEGORIES.items():
        for symbol in symbols:
            try:
                frame, source_status = dual.fetch_fund_daily(
                    symbol, "20200101", "20261231")
                written = repo.upsert_daily(frame, symbol)
                coverage = repo.asset_coverage(symbol)
                liq = repo.liquidity_20d_avg([symbol])
                rows = frame if frame is not None else None
                fetched = int(len(frame)) if rows is not None else 0
                adj_missing = int(len(frame) - frame["adj_factor"].notna().sum()) if fetched else 0
                # 有效交易日 = 有 close 的行
                valid_days = int(frame["close"].notna().sum()) if fetched else 0
                list_date = None  # 由候选名录补
                summary["assets"][symbol] = {
                    "category": category, "fetched": fetched, "written": written,
                    "pool_rows": coverage["rows"], "pool_first": coverage["first"],
                    "pool_last": coverage["last"], "source_status": source_status,
                    "source_breakdown": coverage["source_breakdown"],
                    "adj_missing": adj_missing, "valid_days": valid_days,
                    "avg_amount_20d_cny": liq.get(symbol),
                }
                print(f"{symbol} ({category}): fetched={fetched} adj_missing={adj_missing} "
                      f"src={source_status}")
            except Exception as exc:
                summary["errors"][symbol] = f"{type(exc).__name__}: {str(exc)[:120]}"
                print(f"{symbol}: FAILED {summary['errors'][symbol]}")
            time.sleep(0.4)

    # 质量审计入库（名称与上市日从 fund_basic 名录补齐）
    from trading.wealth_lab.provider import OfficialTushareProvider
    official = OfficialTushareProvider()
    registry = {}
    for market in ("E", "O"):
        try:
            frame = official._call("fund_basic", market=market, limit=3000)
            for row in frame.itertuples(index=False):
                registry[str(row.ts_code)] = row
        except Exception:
            pass
        time.sleep(0.3)

    audit_rows = []
    asof = date(2026, 10, 23)
    for symbol, info in summary["assets"].items():
        meta = registry.get(symbol)
        list_date = None
        name = symbol
        delist = None
        if meta is not None:
            name = str(meta.name)
            try:
                list_date = date.fromisoformat(str(meta.list_date)[:10]) if meta.list_date else None
            except Exception:
                list_date = None
            try:
                delist = date.fromisoformat(str(meta.delist_date)[:10]) if meta.delist_date else None
            except Exception:
                delist = None
        expected = _expected_days(list_date, asof)
        valid = info["valid_days"]
        missing_ratio = max(0.0, 1.0 - valid / expected) if expected > 0 else 1.0
        avg_amount = info.get("avg_amount_20d_cny") or 0.0
        notes = []
        if info["adj_missing"] > 0:
            notes.append(f"复权缺失 {info['adj_missing']} 行（按缺失处理，未填充）")
        if meta is None:
            notes.append("fund_basic 名录未匹配（元数据不完整）")
        quality = "QUALIFIED"
        if valid >= 250 and info["adj_missing"] == 0 and missing_ratio <= 0.02 and avg_amount >= 2e7:
            quality = "QUALIFIED"
        elif valid >= 100 and info["adj_missing"] == 0:
            quality = "DEGRADED"
            notes.append("覆盖/流动性未达 QUALIFIED 标准")
        else:
            quality = "REJECTED"
        audit_rows.append({
            "symbol": symbol, "name": name, "category": info["category"],
            "exchange": "SSE" if symbol.endswith(".SH") else "SZSE",
            "list_date": list_date, "delist_date": delist,
            "data_first": date.fromisoformat(info["pool_first"]) if info.get("pool_first") else None,
            "data_last": date.fromisoformat(info["pool_last"]) if info.get("pool_last") else None,
            "valid_days": valid, "expected_days": expected,
            "missing_ratio": round(missing_ratio, 4), "adj_missing": info["adj_missing"],
            "avg_amount_20d_cny": avg_amount, "source_breakdown": info["source_breakdown"],
            "quality_status": quality, "notes": "; ".join(notes),
        })
    upsert_assets(audit_rows)
    counts: dict[str, int] = {}
    for row in audit_rows:
        counts[row["quality_status"]] = counts.get(row["quality_status"], 0) + 1
    summary["quality_counts"] = counts
    summary["audit_rows"] = len(audit_rows)
    from pathlib import Path
    Path("runtime/_v3_backfill_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("\nquality:", counts, "| audit rows:", len(audit_rows))


if __name__ == "__main__":
    main()
