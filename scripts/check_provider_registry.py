from __future__ import annotations

from pathlib import Path

from router.providers import (
    LongCatProvider,
    QwenProvider,
)
from router.registry import list_roles
from router.services.provider_registry import (
    get_model_provider,
    list_provider_names,
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


def check_provider_registry() -> None:
    provider_names = list_provider_names()

    print("Provider registry:")
    print(
        f"provider_names={list(provider_names)}"
    )

    require(
        provider_names
        == (
            "longcat",
            "qwen",
        ),
        "Provider 注册表内容异常",
    )

    longcat = get_model_provider(
        "longcat"
    )

    qwen = get_model_provider(
        "qwen"
    )

    require(
        isinstance(
            longcat,
            LongCatProvider,
        ),
        "longcat Provider 类型错误",
    )

    require(
        isinstance(
            qwen,
            QwenProvider,
        ),
        "qwen Provider 类型错误",
    )

    require(
        longcat.provider_name
        == "longcat",
        "LongCat provider_name 错误",
    )

    require(
        qwen.provider_name
        == "qwen",
        "Qwen provider_name 错误",
    )

    require(
        get_model_provider(
            "missing_provider"
        )
        is None,
        "未知 Provider 不应返回实例",
    )


def check_enabled_role_alignment() -> None:
    roles = list_roles()

    enabled_roles = [
        role
        for role in roles
        if role.enabled
    ]

    provider_names = set(
        list_provider_names()
    )

    missing_enabled_providers = [
        (
            role.role,
            role.provider,
        )
        for role in enabled_roles
        if role.provider not in provider_names
    ]

    print("\nEnabled role alignment:")
    print(
        "enabled_roles="
        f"{[role.role for role in enabled_roles]}"
    )
    print(
        "enabled_providers="
        f"{[role.provider for role in enabled_roles]}"
    )
    print(
        "missing_enabled_providers="
        f"{missing_enabled_providers}"
    )

    require(
        not missing_enabled_providers,
        (
            "存在已启用但没有 Provider 实现的角色："
            f"{missing_enabled_providers}"
        ),
    )


def check_invocation_architecture() -> None:
    source = INVOCATION_PATH.read_text(
        encoding="utf-8"
    )

    uses_provider_registry = (
        "get_model_provider" in source
    )

    imports_longcat_directly = (
        "LongCatProvider" in source
    )

    has_invoke_longcat_method = (
        "_invoke_longcat" in source
    )

    hardcodes_longcat_provider = (
        'role.provider != "longcat"'
        in source
    )

    has_generic_provider_method = (
        "_invoke_provider" in source
    )

    print("\nInvocation architecture:")
    print(
        "uses_provider_registry="
        f"{uses_provider_registry}"
    )
    print(
        "imports_longcat_directly="
        f"{imports_longcat_directly}"
    )
    print(
        "has_invoke_longcat_method="
        f"{has_invoke_longcat_method}"
    )
    print(
        "hardcodes_longcat_provider="
        f"{hardcodes_longcat_provider}"
    )
    print(
        "has_generic_provider_method="
        f"{has_generic_provider_method}"
    )

    require(
        uses_provider_registry,
        "RouterInvocationService 未使用 Provider 注册表",
    )

    require(
        not imports_longcat_directly,
        (
            "RouterInvocationService 仍直接依赖"
            " LongCatProvider"
        ),
    )

    require(
        not has_invoke_longcat_method,
        "仍存在 _invoke_longcat 专用方法",
    )

    require(
        not hardcodes_longcat_provider,
        "仍存在 LongCat Provider 硬编码分支",
    )

    require(
        has_generic_provider_method,
        "缺少通用 _invoke_provider 方法",
    )


def check_announcement_pipeline_isolation() -> None:
    path = (
        PROJECT_ROOT
        / "router"
        / "services"
        / "announcement_pipeline.py"
    )

    source = path.read_text(
        encoding="utf-8"
    )

    has_audited_qwen = (
        "class AuditedQwenProvider"
        in source
    )

    uses_qwen_provider = (
        "QwenProvider" in source
    )

    uses_generic_registry = (
        "get_model_provider" in source
    )

    print("\nAnnouncement pipeline isolation:")
    print(
        "has_audited_qwen="
        f"{has_audited_qwen}"
    )
    print(
        "uses_qwen_provider="
        f"{uses_qwen_provider}"
    )
    print(
        "uses_generic_registry="
        f"{uses_generic_registry}"
    )

    require(
        has_audited_qwen,
        (
            "公告流水线的 AuditedQwenProvider "
            "被意外移除"
        ),
    )

    require(
        uses_qwen_provider,
        "公告流水线不再使用 QwenProvider",
    )

    require(
        not uses_generic_registry,
        (
            "公告专用流水线不应在本步骤"
            "改用通用 Provider 注册表"
        ),
    )


def main() -> None:
    check_provider_registry()
    check_enabled_role_alignment()
    check_invocation_architecture()
    check_announcement_pipeline_isolation()

    print(
        "\nProvider registry checks passed"
    )


if __name__ == "__main__":
    main()