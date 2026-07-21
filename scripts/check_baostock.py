from __future__ import annotations

import json
import time

import baostock as bs


print("正在连接 BaoStock……", flush=True)
started = time.perf_counter()

login_result = bs.login()

if login_result.error_code != "0":
    raise RuntimeError(
        f"BaoStock 登录失败：{login_result.error_code} "
        f"{login_result.error_msg}"
    )

try:
    query = bs.query_history_k_data_plus(
        "sh.600172",
        "date,code,open,high,low,close,volume,amount,adjustflag",
        start_date="2026-07-01",
        end_date="2026-07-15",
        frequency="d",
        adjustflag="3",
    )

    if query.error_code != "0":
        raise RuntimeError(
            f"BaoStock 查询失败：{query.error_code} {query.error_msg}"
        )

    rows = []

    while query.error_code == "0" and query.next():
        rows.append(query.get_row_data())

    if not rows:
        raise RuntimeError("BaoStock 返回空数据")

    latest = dict(zip(query.fields, rows[-1], strict=False))
    elapsed = round(time.perf_counter() - started, 2)

    result = {
        "records": len(rows),
        "symbol": latest.get("code"),
        "trade_date": latest.get("date"),
        "open": latest.get("open"),
        "high": latest.get("high"),
        "low": latest.get("low"),
        "close": latest.get("close"),
        "volume": latest.get("volume"),
        "elapsed_seconds": elapsed,
    }

    print("BaoStock 测试成功", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

finally:
    bs.logout()