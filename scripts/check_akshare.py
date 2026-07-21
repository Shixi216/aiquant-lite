from __future__ import annotations

import json
import time

import akshare as ak


print("正在连接 AKShare……", flush=True)
started = time.perf_counter()

frame = ak.stock_zh_a_hist(
    symbol="600172",
    period="daily",
    start_date="20260701",
    end_date="20260715",
    adjust="",
)

elapsed = round(time.perf_counter() - started, 2)

if frame.empty:
    raise RuntimeError("AKShare 返回空数据")

required_columns = {"日期", "开盘", "最高", "最低", "收盘", "成交量"}
missing = required_columns.difference(frame.columns)

if missing:
    raise RuntimeError(f"AKShare 缺少字段：{sorted(missing)}")

latest = frame.sort_values("日期").iloc[-1]

result = {
    "records": int(len(frame)),
    "symbol": "600172",
    "trade_date": str(latest["日期"]),
    "open": float(latest["开盘"]),
    "high": float(latest["最高"]),
    "low": float(latest["最低"]),
    "close": float(latest["收盘"]),
    "volume": float(latest["成交量"]),
    "elapsed_seconds": elapsed,
}

print("AKShare 测试成功", flush=True)
print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)