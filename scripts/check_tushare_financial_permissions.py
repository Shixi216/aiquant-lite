from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import tushare as ts

from config.settings import settings


SYMBOL = "600172.SH"
START_DATE = "20240101"
END_DATE = "20260716"


def printable(value: Any) -> str:
    if value is None or pd.isna(value):
        return "None"
    return str(value)


def inspect_statement(
    name: str,
    fetcher: Callable[..., pd.DataFrame],
) -> bool:
    print(f"\n{'=' * 12} {name} {'=' * 12}", flush=True)

    try:
        frame = fetcher(
            ts_code=SYMBOL,
            start_date=START_DATE,
            end_date=END_DATE,
        )
    except Exception as exc:
        print(f"[失败] {name}")
        print(f"错误类型：{type(exc).__name__}")
        print(f"错误信息：{exc}")
        return False

    if frame.empty:
        print(f"[失败] {name}")
        print("接口调用成功，但返回空数据")
        return False

    sort_columns = [
        column
        for column in ("end_date", "ann_date", "f_ann_date")
        if column in frame.columns
    ]

    if sort_columns:
        frame = frame.sort_values(
            sort_columns,
            ascending=False,
        )

    latest = frame.iloc[0]

    print(f"[成功] {name}")
    print(f"记录数：{len(frame)}")
    print(f"字段数：{len(frame.columns)}")

    for field in (
        "ts_code",
        "ann_date",
        "f_ann_date",
        "end_date",
        "report_type",
        "comp_type",
        "update_flag",
    ):
        if field in frame.columns:
            print(f"{field}：{printable(latest[field])}")

    important_fields = {
        "利润表": (
            "total_revenue",
            "revenue",
            "oper_cost",
            "total_profit",
            "income_tax",
            "n_income",
            "n_income_attr_p",
            "basic_eps",
        ),
        "资产负债表": (
            "money_cap",
            "accounts_receiv",
            "inventories",
            "total_cur_assets",
            "total_assets",
            "total_cur_liab",
            "total_liab",
            "total_hldr_eqy_exc_min_int",
        ),
        "现金流量表": (
            "n_cashflow_act",
            "n_cashflow_inv_act",
            "n_cash_flows_fnc_act",
            "n_incr_cash_cash_equ",
            "c_cash_equ_end_period",
        ),
    }

    print("关键字段：")

    for field in important_fields[name]:
        if field in frame.columns:
            print(f"- {field}: {printable(latest[field])}")
        else:
            print(f"- {field}: 字段不存在")

    print("前20个返回字段：")
    print(", ".join(str(column) for column in frame.columns[:20]))

    return True


def main() -> None:
    if not settings.tushare_token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")

    client = ts.pro_api(settings.tushare_token.strip())

    results = [
        inspect_statement("利润表", client.income),
        inspect_statement("资产负债表", client.balancesheet),
        inspect_statement("现金流量表", client.cashflow),
    ]

    success_count = sum(results)

    print(
        f"\n财务报表权限测试结果："
        f"{success_count}/{len(results)} 个接口成功"
    )

    if success_count == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()