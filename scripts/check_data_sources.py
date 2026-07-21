from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from typing import Any, Callable

import akshare as ak
import baostock as bs
import tushare as ts

from config.settings import settings


TEST_SYMBOL_6 = "600172"
TEST_TS_CODE = "600172.SH"
TEST_BAOSTOCK_CODE = "sh.600172"
START_DATE = "20260701"
END_DATE = "20260715"


def safe_value(value: Any) -> Any:
    """Convert pandas/numpy values into printable JSON-compatible values."""
    if value is None:
        return None

    try:
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        pass

    if isinstance(value, (datetime,)):
        return value.isoformat()

    return value


def run_test(name: str, function: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()

    try:
        details = function()
        return {
            "source": name,
            "success": True,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "details": details,
        }
    except Exception as exc:
        return {
            "source": name,
            "success": False,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-5:],
        }


def test_akshare() -> dict[str, Any]:
    frame = ak.stock_zh_a_spot_em()

    if frame.empty:
        raise RuntimeError("AKShare 返回空数据")

    required_columns = {"代码", "名称", "最新价"}
    missing = required_columns.difference(frame.columns)

    if missing:
        raise RuntimeError(f"AKShare 缺少字段：{sorted(missing)}")

    matched = frame.loc[frame["代码"].astype(str) == TEST_SYMBOL_6]

    if matched.empty:
        raise RuntimeError(f"AKShare 未找到股票代码 {TEST_SYMBOL_6}")

    row = matched.iloc[0]

    return {
        "records_returned": int(len(frame)),
        "symbol": TEST_SYMBOL_6,
        "name": safe_value(row["名称"]),
        "latest_price": safe_value(row["最新价"]),
        "change_percent": safe_value(row.get("涨跌幅")),
        "source_note": "AKShare stock_zh_a_spot_em",
    }


def test_tushare() -> dict[str, Any]:
    token = settings.tushare_token

    if not token or not token.strip():
        raise RuntimeError("TUSHARE_TOKEN 未配置")

    pro = ts.pro_api(token.strip())

    frame = pro.daily(
        ts_code=TEST_TS_CODE,
        start_date=START_DATE,
        end_date=END_DATE,
    )

    if frame.empty:
        raise RuntimeError(
            "Tushare 返回空数据；可能是权限不足、日期无数据或接口暂时不可用"
        )

    row = frame.sort_values("trade_date", ascending=False).iloc[0]

    return {
        "records_returned": int(len(frame)),
        "symbol": safe_value(row["ts_code"]),
        "trade_date": safe_value(row["trade_date"]),
        "close": safe_value(row["close"]),
        "volume": safe_value(row["vol"]),
        "source_note": "Tushare Pro daily",
    }


def test_baostock() -> dict[str, Any]:
    login_result = bs.login()

    if login_result.error_code != "0":
        raise RuntimeError(
            f"BaoStock 登录失败：{login_result.error_code} "
            f"{login_result.error_msg}"
        )

    try:
        query = bs.query_history_k_data_plus(
            TEST_BAOSTOCK_CODE,
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

        rows: list[list[str]] = []

        while query.error_code == "0" and query.next():
            rows.append(query.get_row_data())

        if not rows:
            raise RuntimeError("BaoStock 返回空数据")

        latest = rows[-1]
        fields = query.fields
        row = dict(zip(fields, latest, strict=False))

        return {
            "records_returned": len(rows),
            "symbol": row.get("code"),
            "trade_date": row.get("date"),
            "close": row.get("close"),
            "volume": row.get("volume"),
            "source_note": "BaoStock query_history_k_data_plus",
        }
    finally:
        bs.logout()


def main() -> int:
    print("开始验证三套金融数据源，请等待……\n")

    results = [
        run_test("AKShare", test_akshare),
        run_test("Tushare", test_tushare),
        run_test("BaoStock", test_baostock),
    ]

    for result in results:
        status = "成功" if result["success"] else "失败"
        print(f"[{status}] {result['source']}")
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        print()

    output_path = settings.opc_database_path.parent / "data_source_check.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "checked_at": datetime.now().astimezone().isoformat(),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    success_count = sum(item["success"] for item in results)

    print(f"测试结果：{success_count}/3 个数据源成功")
    print(f"检查报告：{output_path}")

    return 0 if success_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
