from __future__ import annotations

from urllib.parse import urlparse

from fastapi.testclient import TestClient

from data_hub.api import app


def main() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/stocks/600172.SH/finance-news",
            params={
                "limit": 20,
                "persist": "false",
            },
        )

        print("财经新闻接口：")
        print(f"状态码：{response.status_code}")

        data = response.json()

        if response.status_code != 200:
            raise RuntimeError(f"接口请求失败：{data}")

        records = data.get("records", [])
        provider_runs = data.get("provider_runs", [])

        print(f"股票代码：{data.get('symbol')}")
        print(f"查询词：{data.get('query')}")
        print(f"新闻数量：{len(records)}")
        print(
            "未核验数量："
            f"{data.get('unverified_count')}"
        )

        print("数据源执行情况：")

        for run in provider_runs:
            status = "成功" if run.get("success") else "失败"

            print(
                f"- {run.get('provider')}: {status}, "
                f"records={run.get('record_count')}, "
                f"latency={run.get('latency_ms')}ms"
            )

            if not run.get("success"):
                print(
                    f"  {run.get('error_type')}: "
                    f"{run.get('error_message')}"
                )

        if data.get("symbol") != "600172.SH":
            raise RuntimeError("股票代码错误")

        if data.get("query") != "600172":
            raise RuntimeError(
                f"默认新闻查询词错误：{data.get('query')}"
            )

        if not records:
            raise RuntimeError("新闻接口没有返回记录")

        latest = records[0]
        payload = latest.get("data", {})
        source_url = str(latest.get("source_url") or "")
        host = (urlparse(source_url).hostname or "").lower()

        print("\n最新新闻：")
        print(f"发布时间：{latest.get('event_time')}")
        print(f"新闻标题：{payload.get('title')}")
        print(f"文章来源：{payload.get('publisher')}")
        print(f"新闻链接：{source_url}")
        print(f"链接域名：{host}")
        print(f"来源等级：{latest.get('source_level')}")
        print(f"是否核验：{latest.get('verified')}")

        if not payload.get("title"):
            raise RuntimeError("最新新闻标题为空")

        if not payload.get("publisher"):
            raise RuntimeError("最新新闻文章来源为空")

        if not source_url or not host:
            raise RuntimeError("最新新闻链接无效")

        if latest.get("source_level") != "media":
            raise RuntimeError(
                "新闻来源等级不是 media"
            )

        if latest.get("verified") is not False:
            raise RuntimeError(
                "媒体新闻不应直接标记为已核验"
            )

        if data.get("unverified_count") != len(records):
            raise RuntimeError(
                "未核验数量和新闻记录数不一致"
            )

        invalid_limit_response = client.get(
            "/v1/stocks/600172.SH/finance-news",
            params={
                "limit": 0,
                "persist": "false",
            },
        )

        print("\n无效 limit 检查：")
        print(
            f"状态码："
            f"{invalid_limit_response.status_code}"
        )
        print(
            "返回内容："
            f"{invalid_limit_response.json()}"
        )

        if invalid_limit_response.status_code != 422:
            raise RuntimeError(
                "limit=0 没有返回 422"
            )

        invalid_symbol_response = client.get(
            "/v1/stocks/INVALID/finance-news",
            params={
                "limit": 10,
                "persist": "false",
            },
        )

        print("\n无效股票代码检查：")
        print(
            f"状态码："
            f"{invalid_symbol_response.status_code}"
        )
        print(
            "返回内容："
            f"{invalid_symbol_response.json()}"
        )

        if invalid_symbol_response.status_code != 422:
            raise RuntimeError(
                "无效股票代码没有返回 422"
            )

    print("\nget_finance_news HTTP 接口检查通过")


if __name__ == "__main__":
    main()