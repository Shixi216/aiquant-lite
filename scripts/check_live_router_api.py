from __future__ import annotations

import httpx


BASE_URL = "http://127.0.0.1:8765"

EXPECTED_ROLES = {
    "announcement_verifier",
    "adversarial_reviewer",
    "vision_reader",
    "risk_controller",
    "complex_vision",
    "news_processor",
}

EXPECTED_PATHS = {
    "/health",
    "/v1/roles",
    "/v1/roles/{role_name}",
}


def main() -> None:
    with httpx.Client(
        base_url=BASE_URL,
        timeout=15,
        trust_env=False,
    ) as client:
        root_response = client.get("/")
        root_data = root_response.json()

        print("真实 Router 根路径：")
        print(f"状态码：{root_response.status_code}")
        print(f"服务名称：{root_data.get('service')}")

        if root_response.status_code != 200:
            raise RuntimeError(
                f"Router 根路径失败：{root_data}"
            )

        if root_data.get("service") != (
            "Hermes OPC Agent Router"
        ):
            raise RuntimeError("8765 端口服务身份不正确")

        health_response = client.get("/health")
        health_data = health_response.json()

        print("\n真实 Router 健康检查：")
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

        if health_data.get("status") != "ok":
            raise RuntimeError("Router 状态不是 ok")

        if health_data.get("data_hub", {}).get("status") != "ok":
            raise RuntimeError("Router 无法访问 Data Hub")

        roles_response = client.get("/v1/roles")
        roles_data = roles_response.json()
        roles = roles_data.get("roles", [])

        print("\n真实 Router 角色清单：")
        print(f"状态码：{roles_response.status_code}")
        print(f"角色数量：{roles_data.get('count')}")
        print(f"启用数量：{roles_data.get('enabled_count')}")

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
            raise RuntimeError("角色清单接口失败")

        if role_names != EXPECTED_ROLES:
            raise RuntimeError(
                f"角色清单不完整：{sorted(role_names)}"
            )

        if roles_data.get("count") != 6:
            raise RuntimeError("角色数量不是6")

        if roles_data.get("enabled_count") != 0:
            raise RuntimeError(
                "尚未配置模型，不应启用角色"
            )

        openapi_response = client.get("/openapi.json")
        openapi_data = openapi_response.json()
        paths = set(openapi_data.get("paths", {}))

        print("\nRouter OpenAPI 路由：")

        for path in sorted(EXPECTED_PATHS):
            exists = path in paths
            print(f"- {path}: {exists}")

        missing_paths = EXPECTED_PATHS.difference(paths)

        if missing_paths:
            raise RuntimeError(
                f"Router 缺少路由：{sorted(missing_paths)}"
            )

        detail_response = client.get(
            "/v1/roles/news_processor"
        )
        detail_data = detail_response.json()

        print("\n角色详情：")
        print(f"状态码：{detail_response.status_code}")
        print(f"角色：{detail_data.get('role')}")
        print(f"模型：{detail_data.get('preferred_model')}")

        if detail_response.status_code != 200:
            raise RuntimeError("角色详情接口失败")

    print("\n真实 Agent Router HTTP 服务检查通过")


if __name__ == "__main__":
    main()