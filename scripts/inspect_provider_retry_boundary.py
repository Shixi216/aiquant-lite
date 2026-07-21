from __future__ import annotations

import ast
import inspect
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

FILES = (
    "router/providers/longcat.py",
    "router/providers/qwen.py",
    "router/services/invocation.py",
    "router/services/announcement_pipeline.py",
)

SEARCH_TERMS = (
    "timeout",
    "Timeout",
    "ReadTimeout",
    "ConnectTimeout",
    "HTTPStatusError",
    "raise_for_status",
    "status_code",
    "tenacity",
    "retry",
    "backoff",
    "sleep",
    "AsyncClient",
    "record_model_call",
)


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def load_text(
    relative_path: str,
) -> str:
    path = PROJECT_ROOT / relative_path

    require(
        path.exists(),
        f"文件不存在：{relative_path}",
    )

    return path.read_text(
        encoding="utf-8"
    )


def print_matching_lines(
    relative_path: str,
) -> None:
    source = load_text(relative_path)
    lines = source.splitlines()

    print(f"\n=== MATCHES {relative_path} ===")

    match_count = 0

    for line_number, line in enumerate(
        lines,
        start=1,
    ):
        if not any(
            term in line
            for term in SEARCH_TERMS
        ):
            continue

        match_count += 1

        start = max(
            1,
            line_number - 2,
        )
        end = min(
            len(lines),
            line_number + 3,
        )

        print(
            f"\n--- around line {line_number} ---"
        )

        for current in range(
            start,
            end + 1,
        ):
            marker = (
                ">"
                if current == line_number
                else " "
            )

            print(
                f"{marker} {current:04d}: "
                f"{lines[current - 1]}"
            )

    print(f"\nmatch_count={match_count}")


def print_exception_handlers(
    relative_path: str,
) -> None:
    source = load_text(relative_path)
    tree = ast.parse(source)

    print(
        f"\n=== EXCEPTION HANDLERS "
        f"{relative_path} ==="
    )

    handler_count = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue

        handler_count += 1

        if node.type is None:
            exception_name = "<bare except>"
        else:
            exception_name = ast.unparse(
                node.type
            )

        print(
            f"line={node.lineno} "
            f"exception={exception_name}"
        )

        for statement in node.body:
            rendered = ast.unparse(
                statement
            )

            print(
                "  "
                f"{rendered[:500]}"
            )

    print(f"handler_count={handler_count}")


def print_async_methods(
    relative_path: str,
) -> None:
    source = load_text(relative_path)
    tree = ast.parse(source)

    print(
        f"\n=== ASYNC METHODS "
        f"{relative_path} ==="
    )

    for node in ast.walk(tree):
        if not isinstance(
            node,
            ast.AsyncFunctionDef,
        ):
            continue

        arguments = ast.unparse(node.args)

        print(
            f"line={node.lineno} "
            f"{node.name}({arguments})"
        )


def print_runtime_signatures() -> None:
    from router.providers import (
        LongCatProvider,
        QwenProvider,
    )
    from router.services.invocation import (
        RouterInvocationService,
    )

    print("\n=== RUNTIME SIGNATURES ===")

    print(
        "LongCatProvider.invoke="
        f"{inspect.signature(LongCatProvider.invoke)}"
    )

    print(
        "QwenProvider.invoke="
        f"{inspect.signature(QwenProvider.invoke)}"
    )

    invocation_signature = inspect.signature(
        RouterInvocationService._invoke_provider
    )

    print(
        "RouterInvocationService._invoke_provider="
        f"{invocation_signature}"
    )


def print_retry_imports() -> None:
    print("\n=== RETRY IMPORTS ===")

    found: list[str] = []

    for root_name in (
        "router",
        "scripts",
    ):
        root = PROJECT_ROOT / root_name

        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue

            if "archive" in path.parts:
                continue

            source = path.read_text(
                encoding="utf-8"
            )

            for line_number, line in enumerate(
                source.splitlines(),
                start=1,
            ):
                lowered = line.lower()

                if (
                    "tenacity" not in lowered
                    and "retrying" not in lowered
                    and "retry(" not in lowered
                    and "stop_after_attempt" not in lowered
                    and "wait_exponential" not in lowered
                ):
                    continue

                relative = path.relative_to(
                    PROJECT_ROOT
                )

                found.append(
                    f"{relative}:{line_number}:"
                    f"{line.strip()}"
                )

    print(f"reference_count={len(found)}")

    for item in found:
        print(item)


def print_audit_boundary() -> None:
    source = load_text(
        "router/services/invocation.py"
    )

    failed_call_audited = (
        "success=False" in source
        and "record_model_call" in source
    )

    successful_call_audited = (
        "success=True" in source
        and "record_model_call" in source
    )

    generic_provider_method = (
        "async def _invoke_provider"
        in source
    )

    retry_loop_present = any(
        token in source
        for token in (
            "for attempt in",
            "while attempt",
            "AsyncRetrying",
            "@retry",
        )
    )

    print("\n=== GENERIC AUDIT BOUNDARY ===")
    print(
        "generic_provider_method="
        f"{generic_provider_method}"
    )
    print(
        "failed_call_audited="
        f"{failed_call_audited}"
    )
    print(
        "successful_call_audited="
        f"{successful_call_audited}"
    )
    print(
        "retry_loop_present="
        f"{retry_loop_present}"
    )


def main() -> None:
    print("Provider retry boundary inspection")

    for relative_path in FILES:
        print_matching_lines(
            relative_path
        )

        print_exception_handlers(
            relative_path
        )

        print_async_methods(
            relative_path
        )

    print_runtime_signatures()
    print_retry_imports()
    print_audit_boundary()

    print(
        "\nProvider retry boundary "
        "inspection passed"
    )


if __name__ == "__main__":
    main()