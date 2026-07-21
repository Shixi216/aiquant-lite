from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import akshare as ak
import pandas as pd

from config.network import configure_network_policy


SYMBOL = "600172"


def printable(value: Any, limit: int = 300) -> str:
    if value is None:
        return "None"

    try:
        if pd.isna(value):
            return "None"
    except (TypeError, ValueError):
        pass

    text = str(value).strip()

    if len(text) > limit:
        return text[:limit] + "..."

    return text


def main() -> None:
    no_proxy = configure_network_policy()

    print(f"AKShare 版本：{ak.__version__}")
    print(f"NO_PROXY：{no_proxy}")
    print("正在查询东方财富个股新闻……", flush=True)

    try:
        frame = ak.stock_news_em(symbol=SYMBOL)
    except Exception as exc:
        print("[失败] AKShare / 东方财富个股新闻")
        print(f"错误类型：{type(exc).__name__}")
        print(f"错误信息：{exc}")
        raise SystemExit(1) from exc

    print("[接口成功] AKShare / 东方财富个股新闻")
    print(f"记录数：{len(frame)}")
    print(
        "返回字段："
        + ", ".join(str(column) for column in frame.columns)
    )

    if frame.empty:
        print("接口调用成功，但没有返回新闻")
        print("个股新闻接口检查通过，但结果为空")
        return

    required = {
        "关键词",
        "新闻标题",
        "新闻内容",
        "发布时间",
        "文章来源",
        "新闻链接",
    }

    missing = required.difference(frame.columns)

    if missing:
        raise RuntimeError(
            f"个股新闻接口缺少必要字段：{sorted(missing)}"
        )

    frame = frame.copy()
    frame["发布时间"] = pd.to_datetime(
        frame["发布时间"],
        errors="coerce",
    )

    frame = frame.sort_values(
        "发布时间",
        ascending=False,
        na_position="last",
    )

    valid_frame = frame[
        frame["新闻标题"].notna()
        & frame["新闻链接"].notna()
    ]

    if valid_frame.empty:
        raise RuntimeError("没有返回标题和链接均有效的新闻")

    latest = valid_frame.iloc[0]
    source_url = printable(latest["新闻链接"], limit=1000)
    host = (urlparse(source_url).hostname or "").lower()

    print("\n最新新闻：")
    print(f"关键词：{printable(latest['关键词'])}")
    print(f"发布时间：{printable(latest['发布时间'])}")
    print(f"新闻标题：{printable(latest['新闻标题'])}")
    print(f"文章来源：{printable(latest['文章来源'])}")
    print(f"新闻链接：{source_url}")
    print(f"链接域名：{host}")
    print(f"新闻内容：{printable(latest['新闻内容'])}")

    print("\n最近5条新闻：")

    display = valid_frame[
        [
            "发布时间",
            "新闻标题",
            "文章来源",
            "新闻链接",
        ]
    ].head(5)

    print(display.to_string(index=False))

    valid_times = valid_frame["发布时间"].notna().sum()
    valid_sources = (
        valid_frame["文章来源"]
        .dropna()
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )

    print("\n数据质量：")
    print(f"有效标题及链接：{len(valid_frame)}")
    print(f"有效发布时间：{valid_times}")
    print(f"有效文章来源：{valid_sources}")

    print("\n东方财富个股新闻接口检查通过")


if __name__ == "__main__":
    main()