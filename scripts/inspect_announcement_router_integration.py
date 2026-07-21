from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import ModuleType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

MODULE_NAMES = [
    "router.services.audit",
    "router.services.audit_query",
    "router.services.invocation",
    "router.services.news_pipeline",
]

SOURCE_FILES = [
    PROJECT_ROOT / "router" / "api" / "app.py",
    PROJECT_ROOT / "router" / "registry.py",
    PROJECT_ROOT / "router" / "routes" / "news_pipeline.py",
]


def format_signature(
    name: str,
    value: Any,
) -> str:
    try:
        signature = inspect.signature(value)
    except (TypeError, ValueError):
        return f"{name}: <signature unavailable>"

    return f"{name}{signature}"


def inspect_module(module: ModuleType) -> None:
    print(f"\n=== MODULE {module.__name__} ===")

    members = inspect.getmembers(module)

    for name, value in members:
        if name.startswith("_"):
            continue

        if inspect.isclass(value):
            if value.__module__ != module.__name__:
                continue

            print(
                "\nCLASS "
                + format_signature(name, value)
            )

            for method_name, method in inspect.getmembers(
                value,
                predicate=inspect.isfunction,
            ):
                if method_name.startswith("_"):
                    continue

                print(
                    "  METHOD "
                    + format_signature(
                        method_name,
                        method,
                    )
                )

        elif inspect.isfunction(value):
            if value.__module__ != module.__name__:
                continue

            print(
                "FUNCTION "
                + format_signature(name, value)
            )


def print_relevant_source(path: Path) -> None:
    print(f"\n=== SOURCE {path.relative_to(PROJECT_ROOT)} ===")

    if not path.exists():
        print("FILE_NOT_FOUND")
        return

    lines = path.read_text(
        encoding="utf-8"
    ).splitlines()

    keywords = (
        "include_router",
        "APIRouter",
        "@router.",
        "@app.",
        "announcement_verifier",
        "news_processor",
        "enabled",
        "provider",
        "model",
        "AuditStore",
        "create_task",
        "model_call",
        "agent_result",
        "complete",
        "fail",
    )

    selected_indexes: set[int] = set()

    for index, line in enumerate(lines):
        if any(
            keyword.lower() in line.lower()
            for keyword in keywords
        ):
            start = max(0, index - 2)
            end = min(len(lines), index + 3)

            selected_indexes.update(
                range(start, end)
            )

    if not selected_indexes:
        print("NO_RELEVANT_LINES")
        return

    previous_index: int | None = None

    for index in sorted(selected_indexes):
        if (
            previous_index is not None
            and index > previous_index + 1
        ):
            print("...")

        print(f"{index + 1:04d}: {lines[index]}")
        previous_index = index


def main() -> None:
    print("Announcement Router integration inspection")

    for module_name in MODULE_NAMES:
        try:
            module = importlib.import_module(
                module_name
            )
        except Exception as exc:
            print(
                f"\n=== MODULE {module_name} ==="
            )
            print(
                "IMPORT_ERROR="
                f"{type(exc).__name__}: {exc}"
            )
            continue

        inspect_module(module)

    for path in SOURCE_FILES:
        print_relevant_source(path)

    print(
        "\nAnnouncement Router integration "
        "inspection passed"
    )


if __name__ == "__main__":
    main()