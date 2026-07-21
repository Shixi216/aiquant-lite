from __future__ import annotations

import importlib
from typing import Any

from fastapi import FastAPI


def describe_route(
    *,
    index: int,
    route: Any,
    announcement_router: Any,
    news_router: Any,
) -> None:
    print(f"\n--- route {index} ---")
    print(
        "type="
        f"{type(route).__module__}."
        f"{type(route).__qualname__}"
    )
    print(f"repr={route!r}")
    print(f"path={getattr(route, 'path', None)}")
    print(f"methods={getattr(route, 'methods', None)}")
    print(f"name={getattr(route, 'name', None)}")
    print(
        "is_announcement_router="
        f"{route is announcement_router}"
    )
    print(
        "is_news_router="
        f"{route is news_router}"
    )

    nested_routes = getattr(route, "routes", None)

    if isinstance(nested_routes, list):
        print(
            "nested_route_count="
            f"{len(nested_routes)}"
        )

        for nested in nested_routes:
            print(
                "nested="
                f"{type(nested).__name__} "
                f"{getattr(nested, 'methods', None)} "
                f"{getattr(nested, 'path', None)}"
            )


def print_routes(
    title: str,
    app: FastAPI,
    announcement_router: Any,
    news_router: Any,
) -> None:
    print(f"\n=== {title} ===")
    print(f"route_count={len(app.routes)}")

    for index, route in enumerate(app.routes):
        describe_route(
            index=index,
            route=route,
            announcement_router=announcement_router,
            news_router=news_router,
        )


def main() -> None:
    app_module = importlib.import_module(
        "router.api.app"
    )

    announcement_module = importlib.import_module(
        "router.routes.announcement_pipeline"
    )

    news_module = importlib.import_module(
        "router.routes.news_pipeline"
    )

    app = getattr(app_module, "app")
    announcement_router = getattr(
        announcement_module,
        "router",
    )
    news_router = getattr(
        news_module,
        "router",
    )

    print("Bound include_router method:")
    print(
        "type="
        f"{type(app.include_router).__module__}."
        f"{type(app.include_router).__qualname__}"
    )
    print(
        "method_module="
        f"{app.include_router.__module__}"
    )
    print(
        "method_qualname="
        f"{app.include_router.__qualname__}"
    )

    print_routes(
        "CURRENT APP ROUTES",
        app,
        announcement_router,
        news_router,
    )

    fresh_app = FastAPI()
    fresh_app.include_router(
        announcement_router
    )
    fresh_app.include_router(
        news_router
    )

    print_routes(
        "FRESH APP CONTROL",
        fresh_app,
        announcement_router,
        news_router,
    )

    current_announcement_count = sum(
        1
        for route in app.routes
        if (
            getattr(route, "path", None)
            == "/v1/pipelines/"
            "announcement-verification"
            and "POST"
            in (getattr(route, "methods", None) or set())
        )
    )

    fresh_announcement_count = sum(
        1
        for route in fresh_app.routes
        if (
            getattr(route, "path", None)
            == "/v1/pipelines/"
            "announcement-verification"
            and "POST"
            in (getattr(route, "methods", None) or set())
        )
    )

    print("\nComparison:")
    print(
        "current_announcement_count="
        f"{current_announcement_count}"
    )
    print(
        "fresh_announcement_count="
        f"{fresh_announcement_count}"
    )

    print(
        "\nRouter object type inspection passed"
    )


if __name__ == "__main__":
    main()