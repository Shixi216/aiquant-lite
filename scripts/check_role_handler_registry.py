from __future__ import annotations

from pathlib import Path

from router.registry import (
    get_role,
    list_roles,
)
from router.services.news_processor_handler import (
    NewsProcessorHandler,
)
from router.services.role_handler_registry import (
    get_role_handler,
    list_handler_roles,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

INVOCATION_PATH = (
    PROJECT_ROOT
    / "router"
    / "services"
    / "invocation.py"
)


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def check_handler_registry() -> None:
    handler_roles = list_handler_roles()

    print("Role handler registry:")
    print(f"handler_roles={list(handler_roles)}")

    require(
        handler_roles == ("news_processor",),
        "Handler 注册角色集合异常",
    )

    news_handler = get_role_handler(
        "news_processor"
    )

    require(
        isinstance(
            news_handler,
            NewsProcessorHandler,
        ),
        "news_processor Handler 类型错误",
    )

    require(
        get_role_handler(
            "announcement_verifier"
        )
        is None,
        (
            "announcement_verifier 不应在本阶段"
            "接入通用 Handler"
        ),
    )

    require(
        get_role_handler(
            "missing_role"
        )
        is None,
        "未知角色不应返回 Handler",
    )


def check_role_alignment() -> None:
    registered_roles = {
        role.role: role
        for role in list_roles()
    }

    handler_roles = set(
        list_handler_roles()
    )

    enabled_roles = {
        role.role
        for role in registered_roles.values()
        if role.enabled
    }

    missing_handlers = (
        enabled_roles
        - handler_roles
    )

    unknown_handler_roles = (
        handler_roles
        - set(registered_roles)
    )

    print("\nRole alignment:")
    print(
        "registered_roles="
        f"{sorted(registered_roles)}"
    )
    print(
        "enabled_roles="
        f"{sorted(enabled_roles)}"
    )
    print(
        "handler_roles="
        f"{sorted(handler_roles)}"
    )
    print(
        "enabled_without_handler="
        f"{sorted(missing_handlers)}"
    )
    print(
        "unknown_handler_roles="
        f"{sorted(unknown_handler_roles)}"
    )

    require(
        not missing_handlers,
        (
            "存在已启用但没有通用 Handler 的角色："
            f"{sorted(missing_handlers)}"
        ),
    )

    require(
        not unknown_handler_roles,
        (
            "Handler 注册表包含未知角色："
            f"{sorted(unknown_handler_roles)}"
        ),
    )

    news_role = get_role(
        "news_processor"
    )

    require(
        news_role is not None,
        "news_processor 未注册",
    )

    require(
        news_role.provider == "longcat",
        "news_processor Provider 配置错误",
    )


def check_invocation_architecture() -> None:
    source = INVOCATION_PATH.read_text(
        encoding="utf-8"
    )

    print("\nInvocation architecture:")
    print(
        "uses_handler_registry="
        f"{'get_role_handler' in source}"
    )
    print(
        "direct_news_handler_import="
        f"{'news_processor_handler import' in source}"
    )
    hardcoded_news_role_branch = (
        'role.role != "news_processor"'
        in source
    )

    print(
        "hardcoded_news_role_branch="
        f"{hardcoded_news_role_branch}"
    )

    require(
        "get_role_handler" in source,
        "RouterInvocationService 未使用 Handler 注册表",
    )

    require(
        "news_processor_handler import"
        not in source,
        (
            "RouterInvocationService 仍直接依赖"
            " NewsProcessorHandler"
        ),
    )

    require(
        'role.role != "news_processor"'
        not in source,
        "RouterInvocationService 仍硬编码新闻角色分支",
    )


def main() -> None:
    check_handler_registry()
    check_role_alignment()
    check_invocation_architecture()

    print(
        "\nRole handler registry checks passed"
    )


if __name__ == "__main__":
    main()