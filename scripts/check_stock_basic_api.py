from __future__ import annotations

from fastapi.testclient import TestClient

from data_hub.api import app


def main() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/stocks/600172.SH/basic",
            params={"persist": "false"},
        )

        print("股票基础信息接口：")
        print(f"状态码：{response.status_code}")

        data = response.json()

        if response.status_code != 200:
            raise RuntimeError(
                f"接口请求失败：{data}"
            )

        print(f"股票代码：{data.get('symbol')}")
        print(
            "股票名称："
            f"{data.get('record', {}).get('data', {}).get('name')}"
        )
        print(f"核验通过：{data.get('verified')}")

        provider_runs = data.get("provider_runs", [])

        print("数据源执行情况：")

        for run in provider_runs:
            status = "成功" if run.get("success") else "失败"

            print(
                f"- {run.get('provider')}: {status}, "
                f"latency={run.get('latency_ms')}ms"
            )

            if not run.get("success"):
                print(
                    f"  {run.get('error_type')}: "
                    f"{run.get('error_message')}"
                )

        if data.get("symbol") != "600172.SH":
            raise RuntimeError("接口返回的股票代码错误")

        name = (
            data.get("record", {})
            .get("data", {})
            .get("name")
        )

        if name != "黄河旋风":
            raise RuntimeError(
                f"接口返回的股票名称异常：{name}"
            )

        successful_count = sum(
            1
            for run in provider_runs
            if run.get("success")
        )

        if successful_count < 1:
            raise RuntimeError("没有任何基础信息数据源成功")

        if successful_count >= 2 and not data.get("verified"):
            raise RuntimeError(
                "两个数据源均成功，但核验状态不是 True"
            )

        invalid_response = client.get(
            "/v1/stocks/INVALID/basic",
            params={"persist": "false"},
        )

        print("\n无效代码检查：")
        print(f"状态码：{invalid_response.status_code}")
        print(f"返回内容：{invalid_response.json()}")

        if invalid_response.status_code != 422:
            raise RuntimeError(
                "无效股票代码没有返回 422"
            )

    print("\nget_stock_basic HTTP 接口检查通过")


if __name__ == "__main__":
    main()