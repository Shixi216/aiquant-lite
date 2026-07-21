from __future__ import annotations

import asyncio

import httpx

from router.api import app


EXPECTED_ROLES = {
    "announcement_verifier",
    "adversarial_reviewer",
    "vision_reader",
    "risk_controller",
    "complex_vision",
    "news_processor",
}


async def check_router() -> None:
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://router-test",
        timeout=10,
    ) as client:
        root_response = await client.get("/")

        print("Router 根路径：")
        print(f"状态码：{root_response.status_code}")
        print(f"服务名称：{root_response.json().get('service')}")

        if root_response.status_code != 200:
            raise RuntimeError("Router 根路径检查失败")

        health_response = await client.get("/health")
        health_data = health_response.json()

        print("\nRouter 健康检查：")
        print(f"状态码：{health_response.status_code}")
        print(f"Router 状态：{health_data.get('status')}")
        print(
            "Data Hub 状态："
            f"{health_data.get('data_hub', {}).get('status')}"
        )
        print(
            "注册角色数："
            f"{health_data.get('registered_roles')}"
        )
        print(
            "启用角色数："
            f"{health_data.get('enabled_roles')}"
        )

        if health_response.status_code != 200:
            raise RuntimeError(
                f"Router 健康检查失败：{health_data}"
            )

        if health_data.get("data_hub", {}).get("status") != "ok":
            raise RuntimeError("Router 无法访问 Data Hub")

        roles_response = await client.get("/v1/roles")
        roles_data = roles_response.json()

        print("\nRouter 角色清单：")
        print(f"状态码：{roles_response.status_code}")
        print(f"角色数量：{roles_data.get('count')}")
        print(f"启用数量：{roles_data.get('enabled_count')}")

        roles = roles_data.get("roles", [])
        role_names = {
            str(item.get("role"))
            for item in roles
        }

        for item in roles:
            print(
                f"- {item.get('role')} -> "
                f"{item.get('preferred_model')} "
                f"(enabled={item.get('enabled')})"
            )

        if roles_response.status_code != 200:
            raise RuntimeError("Router 角色接口检查失败")

        if role_names != EXPECTED_ROLES:
            raise RuntimeError(
                "Router 角色清单不完整："
                f"{sorted(role_names)}"
            )

        if roles_data.get("count") != 6:
            raise RuntimeError("Router 注册角色数不是6")

        if roles_data.get("enabled_count") != 0:
            raise RuntimeError(
                "尚未配置模型时，不应启用任何角色"
            )

        detail_response = await client.get(
            "/v1/roles/risk_controller"
        )
        detail_data = detail_response.json()

        print("\n角色详情检查：")
        print(f"状态码：{detail_response.status_code}")
        print(f"角色：{detail_data.get('role')}")
        print(f"模型：{detail_data.get('preferred_model')}")

        if detail_response.status_code != 200:
            raise RuntimeError("角色详情接口检查失败")

        missing_response = await client.get(
            "/v1/roles/not_exists"
        )

        print("\n未知角色检查：")
        print(f"状态码：{missing_response.status_code}")
        print(f"返回内容：{missing_response.json()}")

        if missing_response.status_code != 404:
            raise RuntimeError("未知角色没有返回404")

    print("\nAgent Router API 骨架检查通过")


def main() -> None:
    asyncio.run(check_router())


if __name__ == "__main__":
    main()