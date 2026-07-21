from __future__ import annotations

from typing import Any

import akshare as ak
import pandas as pd

from config.network import configure_network_policy


SYMBOL = "600172"
START_DATE = "20260101"
END_DATE = "20260716"


def printable(value: Any) -> str:
    if value is None:
        return "None"

    try:
        if pd.isna(value):
            return "None"
    except (TypeError, ValueError):
        pass

    return str(value)


def main() -> None:
    no_proxy = configure_network_policy()

    print(f"AKShare 版本：{ak.__version__}")
    print(f"NO_PROXY：{no_proxy}")
    print("正在查询巨潮资讯公告……", flush=True)

    try:
        frame = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=SYMBOL,
            market="沪深京",
            keyword="",
            category="",
            start_date=START_DATE,
            end_date=END_DATE,
        )
    except Exception as exc:
        print("[失败] AKShare / 巨潮资讯公告")
        print(f"错误类型：{type(exc).__name__}")
        print(f"错误信息：{exc}")
        raise SystemExit(1) from exc

    print("[接口成功] AKShare / 巨潮资讯公告")
    print(f"记录数：{len(frame)}")
    print(
        "返回字段："
        + ", ".join(str(column) for column in frame.columns)
    )

    if frame.empty:
        print("指定时间范围内没有返回公告")
        print("巨潮资讯公告接口检查通过，但结果为空")
        return

    required = {
        "代码",
        "简称",
        "公告标题",
        "公告时间",
        "公告链接",
    }

    missing = required.difference(frame.columns)

    if missing:
        raise RuntimeError(
            f"巨潮资讯公告缺少必要字段：{sorted(missing)}"
        )

    frame = frame.copy()
    frame["公告时间"] = pd.to_datetime(
        frame["公告时间"],
        errors="coerce",
    )
    frame = frame.sort_values(
        "公告时间",
        ascending=False,
        na_position="last",
    )

    latest = frame.iloc[0]

    print("\n最新公告：")
    print(f"代码：{printable(latest['代码'])}")
    print(f"简称：{printable(latest['简称'])}")
    print(f"公告时间：{printable(latest['公告时间'])}")
    print(f"公告标题：{printable(latest['公告标题'])}")
    print(f"公告链接：{printable(latest['公告链接'])}")

    valid_titles = (
        frame["公告标题"]
        .dropna()
        .astype(str)
        .str.strip()
    )
    valid_links = (
        frame["公告链接"]
        .dropna()
        .astype(str)
        .str.strip()
    )

    if valid_titles.empty:
        raise RuntimeError("没有返回有效公告标题")

    if valid_links.empty:
        raise RuntimeError("没有返回有效公告链接")

    print("\n最近5条公告：")
    print(
        frame[
            [
                "公告时间",
                "公告标题",
                "公告链接",
            ]
        ]
        .head(5)
        .to_string(index=False)
    )

    print("\n巨潮资讯公告接口检查通过")


if __name__ == "__main__":
    main()