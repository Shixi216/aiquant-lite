from __future__ import annotations

from fastapi.testclient import TestClient

from data_hub.api import app


def main() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/stocks/600172.SH/realtime-quote",
            params={"persist": "false"},
        )

        print("实时报价接口：")
        print(f"状态码：{response.status_code}")

        data = response.json()

        if response.status_code != 200:
            raise RuntimeError(f"接口请求失败：{data}")

        record = data.get("record", {})
        payload = record.get("data", {})
        provider_runs = data.get("provider_runs", [])

        print(f"股票代码：{data.get('symbol')}")
        print(f"价格：{payload.get('price')}")
        print(f"数据来源：{record.get('source_name')}")
        print(f"数据时间：{record.get('event_time')}")
        print(f"是否实时：{data.get('is_realtime')}")
        print(f"是否降级：{data.get('fallback_used')}")
        print(f"是否核验：{record.get('verified')}")

        if data.get("warning"):
            print(f"警告：{data.get('warning')}")

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

        price = float(payload.get("price") or 0)

        if price <= 0:
            raise RuntimeError("接口没有返回有效价格")

        is_realtime = bool(data.get("is_realtime"))
        fallback_used = bool(data.get("fallback_used"))

        if is_realtime and fallback_used:
            raise RuntimeError(
                "报价不能同时标记为实时和降级"
            )

        if not is_realtime and not fallback_used:
            raise RuntimeError(
                "非实时报价必须明确标记为降级结果"
            )

        if fallback_used:
            if not record.get("verified"):
                raise RuntimeError(
                    "降级行情没有通过多源核验"
                )

            if not data.get("warning"):
                raise RuntimeError(
                    "降级行情没有返回风险提示"
                )

            if payload.get("quote_type") != (
                "latest_close_fallback"
            ):
                raise RuntimeError(
                    "降级行情类型标记错误"
                )

        invalid_response = client.get(
            "/v1/stocks/INVALID/realtime-quote",
            params={"persist": "false"},
        )

        print("\n无效代码检查：")
        print(f"状态码：{invalid_response.status_code}")
        print(f"返回内容：{invalid_response.json()}")

        if invalid_response.status_code != 422:
            raise RuntimeError(
                "无效股票代码没有返回 422"
            )

    print("\nget_realtime_quote HTTP 接口检查通过")


if __name__ == "__main__":
    main()