from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any

import router.schemas.audit as audit_schema_module
from router.schemas.invocation import (
    RouterInvokeRequest,
)
from router.services.invocation import (
    RouterInvocationService,
)


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


def print_module_classes(
    module: ModuleType,
) -> None:
    print(
        f"\n=== CLASSES IN {module.__name__} ==="
    )

    for name, value in inspect.getmembers(
        module,
        predicate=inspect.isclass,
    ):
        if value.__module__ != module.__name__:
            continue

        print_source(name, value)


def main() -> None:
    print("Router invocation detail inspection")

    print_source(
        "RouterInvocationService",
        RouterInvocationService,
    )

    print_source(
        "RouterInvokeRequest",
        RouterInvokeRequest,
    )

    print_module_classes(
        audit_schema_module
    )

    print(
        "\nRouter invocation detail "
        "inspection passed"
    )


if __name__ == "__main__":
    main()