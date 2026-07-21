from __future__ import annotations

from fastapi.testclient import TestClient

from data_hub.api import app


EXPECTED_CAPABILITIES = {
    "get_stock_basic",
    "get_daily_bars",
    "get_realtime_quote",
    "get_financial_statement",
    "get_announcements",
    "get_finance_news",
}


def main() -> None:
    with TestClient(app) as client:
        root_response = client.get("/")

        print("根路径检查：")
        print(f"状态码：{root_response.status_code}")
        print(f"返回内容：{root_response.json()}")

        if root_response.status_code != 200:
            raise RuntimeError("FastAPI 根路径检查失败")

        health_response = client.get("/health")
        health_data = health_response.json()

        print("\n健康检查：")
        print(f"状态码：{health_response.status_code}")
        print(f"服务状态：{health_data.get('status')}")
        print(
            "数据库状态："
            f"{health_data.get('database', {}).get('status')}"
        )

        if health_response.status_code != 200:
            raise RuntimeError(
                f"数据服务健康检查失败：{health_data}"
            )

        if health_data.get("status") != "ok":
            raise RuntimeError("数据服务状态不是 ok")

        if health_data.get("database", {}).get("status") != "ok":
            raise RuntimeError("DuckDB 状态不是 ok")

        capability_response = client.get(
            "/v1/capabilities"
        )
        capability_data = capability_response.json()

        print("\n能力清单：")
        print(f"状态码：{capability_response.status_code}")
        print(f"能力数量：{capability_data.get('count')}")

        names = {
            item["name"]
            for item in capability_data.get(
                "capabilities",
                []
            )
        }

        for name in sorted(names):
            print(f"- {name}")

        if capability_response.status_code != 200:
            raise RuntimeError("能力清单接口检查失败")

        if names != EXPECTED_CAPABILITIES:
            raise RuntimeError(
                "能力清单不完整："
                f"expected={sorted(EXPECTED_CAPABILITIES)}, "
                f"actual={sorted(names)}"
            )

        if capability_data.get("count") != 6:
            raise RuntimeError("能力数量不是6")

    print("\nFastAPI 数据服务骨架检查通过")


if __name__ == "__main__":
    main()