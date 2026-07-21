from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

INVOCATION_PATH = (
    PROJECT_ROOT
    / "router"
    / "services"
    / "invocation.py"
)

SEARCH_SYMBOLS = {
    "RouterInvocationService",
    "extract_average_confidence",
    "merge_usage",
    "NEWS_PROCESSOR_SYSTEM_PROMPT",
    "NewsOutputValidationError",
    "normalize_news_output",
    "build_news_repair_prompt",
    "LongCatProvider",
}


def print_file_head(
    path: Path,
    line_count: int = 100,
) -> None:
    print(
        f"\n=== FILE HEAD "
        f"{path.relative_to(PROJECT_ROOT)} ==="
    )

    lines = path.read_text(
        encoding="utf-8"
    ).splitlines()

    for number, line in enumerate(
        lines[:line_count],
        start=1,
    ):
        print(f"{number:04d}: {line}")


def print_imports(path: Path) -> None:
    print("\n=== INVOCATION IMPORTS ===")

    source = path.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.Import):
            names = ", ".join(
                alias.name
                + (
                    f" as {alias.asname}"
                    if alias.asname
                    else ""
                )
                for alias in node.names
            )

            print(f"import {names}")

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(
                alias.name
                + (
                    f" as {alias.asname}"
                    if alias.asname
                    else ""
                )
                for alias in node.names
            )

            print(
                f"from {module} import {names}"
            )


def source_references(
    symbol: str,
) -> list[str]:
    references: list[str] = []

    roots = [
        PROJECT_ROOT / "router",
        PROJECT_ROOT / "scripts",
    ]

    for root in roots:
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

                relative = path.relative_to(
                    PROJECT_ROOT
                )

                references.append(
                    f"{relative}:{line_number}:"
                    f"{line.strip()}"
                )

    return references


def print_symbol_references() -> None:
    print("\n=== SYMBOL REFERENCES ===")

    for symbol in sorted(SEARCH_SYMBOLS):
        references = source_references(symbol)

        print(f"\n--- {symbol} ---")
        print(f"reference_count={len(references)}")

        for reference in references[:30]:
            print(reference)


def print_module_members(
    relative_path: str,
) -> None:
    path = PROJECT_ROOT / relative_path

    print(f"\n=== MODULE FILE {relative_path} ===")

    if not path.exists():
        print("FILE_NOT_FOUND")
        return

    source = path.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(
            node,
            (
                ast.ClassDef,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            try:
                signature_source = ast.unparse(
                    node.args
                )
            except Exception:
                signature_source = (
                    "<signature unavailable>"
                )

            kind = type(node).__name__

            print(
                f"{kind} {node.name}"
                f"({signature_source})"
            )


def print_existing_news_scripts() -> None:
    print("\n=== EXISTING NEWS TEST SCRIPTS ===")

    scripts_dir = PROJECT_ROOT / "scripts"

    paths = sorted(
        path
        for path in scripts_dir.glob("*.py")
        if (
            "news" in path.name.lower()
            or "router" in path.name.lower()
            or "invoke" in path.name.lower()
        )
    )

    print(f"script_count={len(paths)}")

    for path in paths:
        print(path.relative_to(PROJECT_ROOT))


def print_class_methods(
    path: Path,
    class_name: str,
) -> None:
    source = path.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    print(f"\n=== CLASS METHODS {class_name} ===")

    for node in tree.body:
        if (
            isinstance(node, ast.ClassDef)
            and node.name == class_name
        ):
            for child in node.body:
                if isinstance(
                    child,
                    (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                    ),
                ):
                    print(
                        f"{type(child).__name__} "
                        f"{child.name}"
                    )

            return

    print("CLASS_NOT_FOUND")


def main() -> None:
    if not INVOCATION_PATH.exists():
        raise RuntimeError(
            "router/services/invocation.py 不存在"
        )

    print("Role Handler boundary inspection")

    print_file_head(
        INVOCATION_PATH,
        line_count=120,
    )

    print_imports(INVOCATION_PATH)

    print_class_methods(
        INVOCATION_PATH,
        "RouterInvocationService",
    )

    print_module_members(
        "router/services/news_output.py"
    )

    print_module_members(
        "router/providers/longcat.py"
    )

    print_module_members(
        "router/schemas/invocation.py"
    )

    print_symbol_references()
    print_existing_news_scripts()

    print(
        "\nRole Handler boundary "
        "inspection passed"
    )


if __name__ == "__main__":
    main()
