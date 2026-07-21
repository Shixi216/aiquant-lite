from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi import FastAPI


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def module_path(module_name: str) -> str:
    module = importlib.import_module(module_name)
    value = getattr(module, "__file__", None)

    if value is None:
        return "<no __file__>"

    return str(Path(value).resolve())


def main() -> None:
    importlib.invalidate_caches()

    app_module = importlib.import_module(
        "router.api.app"
    )
    route_module = importlib.import_module(
        "router.routes.announcement_pipeline"
    )

    app = getattr(app_module, "app", None)

    if not isinstance(app, FastAPI):
        raise RuntimeError(
            "router.api.app.app 不是 FastAPI 实例"
        )

    print("Runtime import paths:")
    print(f"project_root={PROJECT_ROOT}")
    print(f"cwd={Path.cwd().resolve()}")
    print(f"sys_path_0={sys.path[0]}")
    print(
        "router_api_app="
        f"{module_path('router.api.app')}"
    )
    print(
        "announcement_route_module="
        f"{module_path('router.routes.announcement_pipeline')}"
    )

    print("\nAnnouncement router object:")
    announcement_router = getattr(
        route_module,
        "router",
        None,
    )

    print(
        "router_type="
        f"{type(announcement_router).__name__}"
    )

    if announcement_router is not None:
        for route in announcement_router.routes:
            print(
                "local_route="
                f"{getattr(route, 'methods', None)} "
                f"{getattr(route, 'path', None)} "
                f"name={getattr(route, 'name', None)}"
            )

    print("\nAll Agent Router routes:")

    found = 0

    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        name = getattr(route, "name", None)

        print(
            f"route={methods} {path} name={name}"
        )

        if (
            path
            == "/v1/pipelines/announcement-verification"
            and methods
            and "POST" in methods
        ):
            found += 1

    print("\nExact route result:")
    print(f"announcement_route_count={found}")

    print(
        "\nRuntime route registry inspection passed"
    )


if __name__ == "__main__":
    main()