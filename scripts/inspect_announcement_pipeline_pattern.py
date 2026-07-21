from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

import router.routes.news_pipeline as news_route_module
from router.registry import _role_enabled
from router.schemas import (
    NewsPipelineRequest,
    NewsPipelineResponse,
    RouterInvokeResponse,
)
from router.services.audit import AuditStore
from router.services.news_pipeline import (
    NewsAnalysisPipelineService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def print_source(
    title: str,
    value: Any,
) -> None:
    print(f"\n=== {title} ===")

    try:
        source = inspect.getsource(value)
    except (OSError, TypeError) as exc:
        print(
            "SOURCE_ERROR="
            f"{type(exc).__name__}: {exc}"
        )
        return

    print(source.rstrip())


def print_file_ranges(
    relative_path: str,
    ranges: list[tuple[int, int]],
) -> None:
    path = PROJECT_ROOT / relative_path

    print(f"\n=== FILE {relative_path} ===")

    if not path.exists():
        print("FILE_NOT_FOUND")
        return

    lines = path.read_text(
        encoding="utf-8"
    ).splitlines()

    for start, end in ranges:
        print(f"\n--- lines {start}-{end} ---")

        actual_end = min(end, len(lines))

        for line_number in range(
            start,
            actual_end + 1,
        ):
            print(
                f"{line_number:04d}: "
                f"{lines[line_number - 1]}"
            )


def main() -> None:
    print("Announcement pipeline pattern inspection")

    print_source(
        "AuditStore",
        AuditStore,
    )

    print_source(
        "NewsAnalysisPipelineService",
        NewsAnalysisPipelineService,
    )

    print_source(
        "NewsPipelineRequest",
        NewsPipelineRequest,
    )

    print_source(
        "NewsPipelineResponse",
        NewsPipelineResponse,
    )

    print_source(
        "RouterInvokeResponse",
        RouterInvokeResponse,
    )

    print_source(
        "news pipeline route module",
        news_route_module,
    )

    print_source(
        "registry _role_enabled",
        _role_enabled,
    )

    app_module = importlib.import_module(
        "router.api.app"
    )
    app_file = getattr(
        app_module,
        "__file__",
        None,
    )

    if app_file is None:
        raise RuntimeError(
            "无法定位 router.api.app 模块文件"
        )

    app_path = Path(app_file).resolve()

    print_file_ranges(
        str(app_path.relative_to(PROJECT_ROOT)),
        [
            (1, 45),
            (125, 215),
        ],
    )

    print(
        "\nAnnouncement pipeline pattern "
        "inspection passed"
    )


if __name__ == "__main__":
    main()
