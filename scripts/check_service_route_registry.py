from __future__ import annotations

import importlib
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from router.registry import get_role, list_roles


PROJECT_ROOT = Path(__file__).resolve().parents[1]

HTTP_METHODS = {
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "HEAD",
}

EXPECTED_ROUTER_OPERATIONS = {
    (
        "/v1/pipelines/news-analysis",
        "POST",
    ),
    (
        "/v1/pipelines/announcement-verification",
        "POST",
    ),
    (
        "/v1/invoke",
        "POST",
    ),
    (
        "/v1/audit/tasks",
        "GET",
    ),
    (
        "/v1/audit/tasks/{task_id}",
        "GET",
    ),
}

EXPECTED_DATA_HUB_OPERATIONS = {
    (
        "/v1/stocks/{symbol}/announcements",
        "GET",
    ),
    (
        "/v1/stocks/{symbol}/announcements/resolve",
        "GET",
    ),
}

PATCH_FILENAMES = {
    "patch_announcement_pipeline_app.py",
    "patch_announcement_selector.py",
}

REQUIRED_ARCHIVED_PATCH_FILENAMES = {
    "patch_announcement_pipeline_app.py",
}

ACTIVE_SCRIPTS_DIR = PROJECT_ROOT / "scripts"

PATCH_ARCHIVE_DIR = (
    PROJECT_ROOT
    / "scripts"
    / "archive"
    / "one_time_patches"
)


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def load_fastapi_app(
    module_name: str,
) -> FastAPI:
    module = importlib.import_module(module_name)
    app = getattr(module, "app", None)

    require(
        isinstance(app, FastAPI),
        f"{module_name}.app 不是 FastAPI 实例",
    )

    return app


def openapi_operations(
    app: FastAPI,
) -> set[tuple[str, str]]:
    schema = app.openapi()
    paths = schema.get("paths")

    require(
        isinstance(paths, dict),
        "OpenAPI Schema 缺少 paths 对象",
    )

    operations: set[tuple[str, str]] = set()

    for raw_path, raw_path_item in paths.items():
        if not isinstance(raw_path, str):
            continue

        if not isinstance(raw_path_item, dict):
            continue

        for raw_method in raw_path_item:
            method = str(raw_method).upper()

            if method not in HTTP_METHODS:
                continue

            operations.add(
                (
                    raw_path,
                    method,
                )
            )

    return operations


def check_expected_operations(
    *,
    service_name: str,
    app: FastAPI,
    expected: set[tuple[str, str]],
) -> None:
    operations = openapi_operations(app)

    print(f"\n{service_name} OpenAPI operations:")

    for path, method in sorted(expected):
        present = (path, method) in operations

        print(
            f"- {method} {path}: "
            f"present={present}"
        )

        require(
            present,
            (
                f"{service_name} 缺少 OpenAPI 操作："
                f"{method} {path}"
            ),
        )


def response_detail(
    response: Any,
) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]

    if isinstance(payload, dict):
        detail = payload.get("detail")

        if detail is not None:
            return str(detail)[:300]

    return str(payload)[:300]


def check_router_dispatch(
    app: FastAPI,
) -> None:
    probes: list[
        tuple[
            str,
            str,
            dict[str, Any],
            int,
        ]
    ] = [
        (
            "POST",
            "/v1/pipelines/"
            "announcement-verification",
            {
                "json": {},
            },
            422,
        ),
        (
            "POST",
            "/v1/pipelines/news-analysis",
            {
                "json": {},
            },
            422,
        ),
        (
            "POST",
            "/v1/invoke",
            {
                "json": {},
            },
            422,
        ),
        (
            "GET",
            "/v1/audit/tasks",
            {
                "params": {
                    "limit": 0,
                },
            },
            422,
        ),
    ]

    print("\nAgent Router dispatch probes:")

    with TestClient(app) as client:
        for method, path, kwargs, expected in probes:
            response = client.request(
                method,
                path,
                **kwargs,
            )

            print(
                f"- {method} {path}: "
                f"status={response.status_code}"
            )

            require(
                response.status_code == expected,
                (
                    "Agent Router 路由分发异常："
                    f"{method} {path}, "
                    f"expected={expected}, "
                    f"actual={response.status_code}, "
                    f"detail={response_detail(response)}"
                ),
            )


def check_data_hub_dispatch(
    app: FastAPI,
) -> None:
    probes: list[
        tuple[
            str,
            str,
            dict[str, Any],
            int,
        ]
    ] = [
        (
            "GET",
            "/v1/stocks/600172.SH/"
            "announcements",
            {},
            422,
        ),
        (
            "GET",
            "/v1/stocks/600172.SH/"
            "announcements/resolve",
            {},
            422,
        ),
    ]

    print("\nData Hub dispatch probes:")

    with TestClient(app) as client:
        for method, path, kwargs, expected in probes:
            response = client.request(
                method,
                path,
                **kwargs,
            )

            print(
                f"- {method} {path}: "
                f"status={response.status_code}"
            )

            require(
                response.status_code == expected,
                (
                    "Data Hub 路由分发异常："
                    f"{method} {path}, "
                    f"expected={expected}, "
                    f"actual={response.status_code}, "
                    f"detail={response_detail(response)}"
                ),
            )


def check_roles() -> None:
    roles = list_roles()

    role_names = [
        role.role
        for role in roles
    ]

    print("\nRouter roles:")
    print(f"registered_count={len(roles)}")
    print(f"role_names={role_names}")

    require(
        len(roles) == 6,
        "Router 注册角色数量异常",
    )

    require(
        len(role_names) == len(set(role_names)),
        "Router 存在重复角色",
    )

    announcement_role = get_role(
        "announcement_verifier"
    )

    news_role = get_role(
        "news_processor"
    )

    require(
        announcement_role is not None,
        "announcement_verifier 未注册",
    )

    require(
        news_role is not None,
        "news_processor 未注册",
    )

    require(
        announcement_role.provider == "qwen",
        "announcement_verifier Provider 配置错误",
    )

    require(
        news_role.provider == "longcat",
        "news_processor Provider 配置错误",
    )

    print(
        "announcement_verifier_provider="
        f"{announcement_role.provider}"
    )

    print(
        "announcement_verifier_enabled="
        f"{announcement_role.enabled}"
    )

    print(
        "news_processor_provider="
        f"{news_role.provider}"
    )

    print(
        "news_processor_enabled="
        f"{news_role.enabled}"
    )


def check_no_active_patch_sources() -> None:
    active_paths = [
        ACTIVE_SCRIPTS_DIR / filename
        for filename in PATCH_FILENAMES
    ]

    existing_paths = [
        str(path)
        for path in active_paths
        if path.exists()
    ]

    print("\nActive patch source files:")
    print(f"count={len(existing_paths)}")
    print(f"files={existing_paths}")

    require(
        not existing_paths,
        "一次性补丁源码仍位于活动 scripts 目录",
    )


def check_patch_archive() -> None:
    archived_paths = [
        PATCH_ARCHIVE_DIR / filename
        for filename in REQUIRED_ARCHIVED_PATCH_FILENAMES
    ]

    missing_paths = [
        str(path)
        for path in archived_paths
        if not path.exists()
    ]

    readme_path = PATCH_ARCHIVE_DIR / "README.md"

    print("\nArchived patch files:")
    print(f"archive_dir={PATCH_ARCHIVE_DIR}")

    for path in archived_paths:
        print(
            f"- {path.name}: exists={path.exists()}"
        )

    print(
        "README.md: "
        f"exists={readme_path.exists()}"
    )

    require(
        PATCH_ARCHIVE_DIR.is_dir(),
        "一次性补丁归档目录不存在",
    )

    require(
        not missing_paths,
        (
            "一次性补丁未完整归档："
            f"{missing_paths}"
        ),
    )

    require(
        readme_path.exists(),
        "一次性补丁归档目录缺少 README.md",
    )


def check_no_patch_bytecode() -> None:
    cache_dir = ACTIVE_SCRIPTS_DIR / "__pycache__"

    residual_files: list[str] = []

    if cache_dir.exists():
        for filename in PATCH_FILENAMES:
            module_name = Path(filename).stem

            residual_files.extend(
                str(path)
                for path in cache_dir.glob(
                    f"{module_name}.cpython-*.pyc"
                )
            )

    print("\nResidual patch bytecode:")
    print(f"count={len(residual_files)}")
    print(f"files={residual_files}")

    require(
        not residual_files,
        "活动 scripts/__pycache__ 中仍有补丁字节码",
    )


def main() -> None:
    importlib.invalidate_caches()

    router_app = load_fastapi_app(
        "router.api.app"
    )

    data_hub_app = load_fastapi_app(
        "data_hub.api.app"
    )

    print("Framework version:")
    print(f"fastapi={version('fastapi')}")

    check_expected_operations(
        service_name="Agent Router",
        app=router_app,
        expected=EXPECTED_ROUTER_OPERATIONS,
    )

    check_expected_operations(
        service_name="Data Hub",
        app=data_hub_app,
        expected=EXPECTED_DATA_HUB_OPERATIONS,
    )

    check_router_dispatch(router_app)
    check_data_hub_dispatch(data_hub_app)
    check_roles()
    check_no_active_patch_sources()
    check_patch_archive()
    check_no_patch_bytecode()

    print(
        "\nService route registry checks passed"
    )


if __name__ == "__main__":
    main()