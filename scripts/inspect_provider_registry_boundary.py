from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROVIDER_TARGETS = (
    (
        "router.providers.longcat",
        "LongCatProvider",
    ),
    (
        "router.providers.qwen",
        "QwenProvider",
    ),
)

SEARCH_SYMBOLS = {
    "LongCatProvider",
    "QwenProvider",
    "AuditedQwenProvider",
    "provider_name",
    "preferred_model",
    "_invoke_longcat",
}

SEARCH_ROOTS = (
    PROJECT_ROOT / "router",
    PROJECT_ROOT / "scripts",
)


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def relative_path(path: Path) -> str:
    try:
        return str(
            path.relative_to(PROJECT_ROOT)
        )
    except ValueError:
        return str(path)


def print_file_head(
    relative: str,
    line_count: int = 120,
) -> None:
    path = PROJECT_ROOT / relative

    print(f"\n=== FILE HEAD {relative} ===")

    if not path.exists():
        print("FILE_NOT_FOUND")
        return

    lines = path.read_text(
        encoding="utf-8"
    ).splitlines()

    for number, line in enumerate(
        lines[:line_count],
        start=1,
    ):
        print(f"{number:04d}: {line}")


def print_ast_members(
    relative: str,
) -> None:
    path = PROJECT_ROOT / relative

    print(f"\n=== AST MEMBERS {relative} ===")

    if not path.exists():
        print("FILE_NOT_FOUND")
        return

    source = path.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            print(f"ClassDef {node.name}")

            for child in node.body:
                if isinstance(
                    child,
                    (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                    ),
                ):
                    print(
                        "  "
                        f"{type(child).__name__} "
                        f"{child.name}"
                    )

        elif isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            print(
                f"{type(node).__name__} "
                f"{node.name}"
            )


def print_runtime_provider(
    module_name: str,
    class_name: str,
) -> None:
    print(
        "\n=== RUNTIME PROVIDER "
        f"{module_name}.{class_name} ==="
    )

    module = importlib.import_module(
        module_name
    )

    provider_class = getattr(
        module,
        class_name,
        None,
    )

    require(
        isinstance(provider_class, type),
        (
            f"{module_name}.{class_name} "
            "不是可用的类"
        ),
    )

    module_file = getattr(
        module,
        "__file__",
        None,
    )

    print(
        "module_file="
        f"{relative_path(Path(module_file).resolve())}"
        if module_file
        else "module_file=<none>"
    )

    print(
        "class_signature="
        f"{inspect.signature(provider_class)}"
    )

    invoke_method = getattr(
        provider_class,
        "invoke",
        None,
    )

    require(
        callable(invoke_method),
        f"{class_name} 缺少 invoke 方法",
    )

    print(
        "invoke_signature="
        f"{inspect.signature(invoke_method)}"
    )

    for attribute_name in (
        "provider_name",
        "model_name",
    ):
        attribute = getattr(
            provider_class,
            attribute_name,
            "<missing>",
        )

        print(
            f"class_{attribute_name}="
            f"{attribute!r}"
        )


def source_references(
    symbol: str,
) -> list[str]:
    references: list[str] = []

    for root in SEARCH_ROOTS:
        if not root.exists():
            continue

        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue

            if "archive" in path.parts:
                continue

            try:
                lines = path.read_text(
                    encoding="utf-8"
                ).splitlines()
            except OSError:
                continue

            for line_number, line in enumerate(
                lines,
                start=1,
            ):
                if symbol not in line:
                    continue

                references.append(
                    f"{relative_path(path)}:"
                    f"{line_number}:"
                    f"{line.strip()}"
                )

    return references


def print_symbol_references() -> None:
    print("\n=== SYMBOL REFERENCES ===")

    for symbol in sorted(SEARCH_SYMBOLS):
        references = source_references(
            symbol
        )

        print(f"\n--- {symbol} ---")
        print(
            f"reference_count={len(references)}"
        )

        for reference in references[:40]:
            print(reference)


def print_role_provider_mapping() -> None:
    from router.registry import list_roles

    print("\n=== ROLE PROVIDER MAPPING ===")

    roles = list_roles()

    for role in roles:
        print(
            f"role={role.role} "
            f"provider={role.provider} "
            f"model={role.preferred_model} "
            f"enabled={role.enabled}"
        )


def print_invocation_architecture() -> None:
    path = (
        PROJECT_ROOT
        / "router"
        / "services"
        / "invocation.py"
    )

    source = path.read_text(
        encoding="utf-8"
    )

    checks = {
        "imports_longcat_directly": (
            "LongCatProvider" in source
        ),
        "has_invoke_longcat_method": (
            "_invoke_longcat" in source
        ),
        "hardcodes_longcat_provider": (
            'role.provider != "longcat"'
            in source
        ),
        "uses_handler_registry": (
            "get_role_handler" in source
        ),
    }

    print("\n=== INVOCATION ARCHITECTURE ===")

    for name, value in checks.items():
        print(f"{name}={value}")


def print_existing_provider_scripts() -> None:
    scripts_dir = PROJECT_ROOT / "scripts"

    paths = sorted(
        path
        for path in scripts_dir.glob("*.py")
        if any(
            token in path.name.lower()
            for token in (
                "provider",
                "longcat",
                "qwen",
                "router",
            )
        )
    )

    print("\n=== EXISTING PROVIDER TEST SCRIPTS ===")
    print(f"script_count={len(paths)}")

    for path in paths:
        print(relative_path(path))


def main() -> None:
    importlib.invalidate_caches()

    print("Provider registry boundary inspection")

    print_file_head(
        "router/providers/longcat.py",
        line_count=140,
    )

    print_file_head(
        "router/providers/qwen.py",
        line_count=140,
    )

    print_file_head(
        "router/providers/__init__.py",
        line_count=100,
    )

    print_ast_members(
        "router/providers/longcat.py"
    )

    print_ast_members(
        "router/providers/qwen.py"
    )

    for module_name, class_name in PROVIDER_TARGETS:
        print_runtime_provider(
            module_name,
            class_name,
        )

    print_role_provider_mapping()
    print_invocation_architecture()
    print_symbol_references()
    print_existing_provider_scripts()

    print(
        "\nProvider registry boundary "
        "inspection passed"
    )


if __name__ == "__main__":
    main()