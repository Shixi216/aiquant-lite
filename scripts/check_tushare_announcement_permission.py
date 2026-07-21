from __future__ import annotations

from typing import Any

import pandas as pd
import tushare as ts

from config.settings import settings


SYMBOL = "600172.SH"
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
    if not settings.tushare_token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")

    client = ts.pro_api(settings.tushare_token.strip())

    print("正在测试 Tushare 上市公司公告接口……", flush=True)

    try:
        frame = client.anns_d(
            ts_code=SYMBOL,
            start_date=START_DATE,
            end_date=END_DATE,
        )
    except Exception as exc:
        print("[失败] Tushare 公告接口")
        print(f"错误类型：{type(exc).__name__}")
        print(f"错误信息：{exc}")
        raise SystemExit(1) from exc

    if frame.empty:
        print("[接口成功] Tushare 公告接口")
        print("指定时间范围内没有返回公告")
        print("Tushare 公告权限检查通过，但当前结果为空")
        return

    sort_columns = [
        column
        for column in ("rec_time", "ann_date")
        if column in frame.columns
    ]

    if sort_columns:
        frame = frame.sort_values(
            sort_columns,
            ascending=False,
            na_position="last",
        )

    latest = frame.iloc[0]

    print("[成功] Tushare 公告接口")
    print(f"记录数：{len(frame)}")
    print(f"字段：{', '.join(str(item) for item in frame.columns)}")

    print("\n最新公告：")

    for field in (
        "ts_code",
        "name",
        "ann_date",
        "rec_time",
        "title",
        "url",
    ):
        if field in frame.columns:
            print(f"{field}：{printable(latest[field])}")

    required_fields = {
        "ts_code",
        "ann_date",
        "title",
        "url",
    }

    missing = required_fields.difference(frame.columns)

    if missing:
        raise RuntimeError(
            f"公告接口缺少必要字段：{sorted(missing)}"
        )

    valid_urls = frame["url"].dropna().astype(str)
    valid_titles = frame["title"].dropna().astype(str)

    if valid_titles.empty:
        raise RuntimeError("公告接口没有返回有效标题")

    if valid_urls.empty:
        raise RuntimeError("公告接口没有返回原文链接")

    print("\n最近5条公告：")

    display_fields = [
        field
        for field in (
            "ann_date",
            "rec_time",
            "title",
            "url",
        )
        if field in frame.columns
    ]

    print(
        frame[display_fields]
        .head(5)
        .to_string(index=False)
    )

    print("\nTushare 公告权限检查通过")


if __name__ == "__main__":
    main()