from __future__ import annotations

from urllib.parse import urlparse

from fastapi.testclient import TestClient

from data_hub.api import app


def main() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/stocks/600172.SH/announcements",
            params={
                "start_date": "20260101",
                "end_date": "20260716",
                "persist": "false",
            },
        )

        print("公告接口：")
        print(f"状态码：{response.status_code}")

        data = response.json()

        if response.status_code != 200:
            raise RuntimeError(f"接口请求失败：{data}")

        records = data.get("records", [])
        provider_runs = data.get("provider_runs", [])

        print(f"股票代码：{data.get('symbol')}")
        print(f"公告数量：{len(records)}")
        print(f"已核验数量：{data.get('verified_count')}")

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

        if not records:
            raise RuntimeError("公告接口没有返回记录")

        latest = records[0]
        payload = latest.get("data", {})
        source_url = str(latest.get("source_url") or "")
        host = (urlparse(source_url).hostname or "").lower()

        print("\n最新公告：")
        print(
            "公告日期："
            f"{payload.get('announcement_date')}"
        )
        print(f"公告标题：{payload.get('title')}")
        print(f"公告链接：{source_url}")
        print(f"是否核验：{latest.get('verified')}")

        if not payload.get("title"):
            raise RuntimeError("最新公告标题为空")

        if not source_url:
            raise RuntimeError("最新公告链接为空")

        if not (
            host == "cninfo.com.cn"
            or host.endswith(".cninfo.com.cn")
        ):
            raise RuntimeError(
                f"公告链接不是巨潮资讯域名：{host}"
            )

        if not latest.get("verified"):
            raise RuntimeError("最新公告未标记为已核验")

        if data.get("verified_count") != len(records):
            raise RuntimeError(
                "公告核验数量和记录数量不一致"
            )

        invalid_response = client.get(
            "/v1/stocks/600172.SH/announcements",
            params={
                "start_date": "20260720",
                "end_date": "20260101",
                "persist": "false",
            },
        )

        print("\n无效日期检查：")
        print(f"状态码：{invalid_response.status_code}")
        print(f"返回内容：{invalid_response.json()}")

        if invalid_response.status_code != 422:
            raise RuntimeError(
                "错误日期范围没有返回 422"
            )

    print("\nget_announcements HTTP 接口检查通过")


if __name__ == "__main__":
    main()