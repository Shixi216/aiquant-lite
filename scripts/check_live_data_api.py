from __future__ import annotations

import httpx


BASE_URL = "http://127.0.0.1:8766"

EXPECTED_PATHS = {
    "/v1/stocks/{symbol}/basic",
    "/v1/stocks/{symbol}/daily-bars",
    "/v1/stocks/{symbol}/realtime-quote",
    "/v1/stocks/{symbol}/financial-statements",
    "/v1/stocks/{symbol}/announcements",
    "/v1/stocks/{symbol}/finance-news",
}


def main() -> None:
    with httpx.Client(
        base_url=BASE_URL,
        timeout=30,
        trust_env=False,
    ) as client:
        health_response = client.get("/health")
        health_data = health_response.json()

        print("真实服务健康检查：")
        print(f"状态码：{health_response.status_code}")
        print(f"服务状态：{health_data.get('status')}")
        print(
            "数据库状态："
            f"{health_data.get('database', {}).get('status')}"
        )

        if health_response.status_code != 200:
            raise RuntimeError(
                f"健康检查失败：{health_data}"
            )

        capability_response = client.get(
            "/v1/capabilities"
        )
        capability_data = capability_response.json()

        print("\n能力清单：")
        print(f"状态码：{capability_response.status_code}")
        print(f"能力数量：{capability_data.get('count')}")

        if capability_response.status_code != 200:
            raise RuntimeError(
                f"能力清单请求失败：{capability_data}"
            )

        if capability_data.get("count") != 6:
            raise RuntimeError("能力数量不是6")

        openapi_response = client.get("/openapi.json")
        openapi_data = openapi_response.json()
        actual_paths = set(openapi_data.get("paths", {}))

        print("\nOpenAPI 路由检查：")

        for path in sorted(EXPECTED_PATHS):
            exists = path in actual_paths
            print(f"- {path}: {exists}")

        missing_paths = EXPECTED_PATHS.difference(actual_paths)

        if missing_paths:
            raise RuntimeError(
                f"缺少 HTTP 路由：{sorted(missing_paths)}"
            )

        basic_response = client.get(
            "/v1/stocks/600172.SH/basic",
            params={"persist": "false"},
        )
        basic_data = basic_response.json()

        print("\n真实股票接口检查：")
        print(f"状态码：{basic_response.status_code}")
        print(f"股票代码：{basic_data.get('symbol')}")
        print(
            "股票名称："
            f"{basic_data.get('record', {}).get('data', {}).get('name')}"
        )
        print(f"核验通过：{basic_data.get('verified')}")

        if basic_response.status_code != 200:
            raise RuntimeError(
                f"股票基础信息接口失败：{basic_data}"
            )

        if basic_data.get("symbol") != "600172.SH":
            raise RuntimeError("股票代码返回错误")

        name = (
            basic_data.get("record", {})
            .get("data", {})
            .get("name")
        )

        if name != "黄河旋风":
            raise RuntimeError(
                f"股票名称返回异常：{name}"
            )

    print("\n真实 Data Hub HTTP 服务检查通过")


if __name__ == "__main__":
    main()