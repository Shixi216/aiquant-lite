from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "backups",
    "checkpoints",
    "crash-reports",
    "logs",
    "reports",
}
FORBIDDEN_SUFFIXES = {
    ".bak",
    ".duckdb",
    ".pyc",
    ".secret",
    ".secrets",
}
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^[ \t]*[A-Z0-9_]*(?:TOKEN|API_KEY|SECRET|PASSWORD|PRIVATE_KEY)"
    r"[ \t]*=[ \t]*(?!$|<[^>]+>$|CHANGE_ME$|YOUR_[A-Z0-9_]+$)(.+)$"
)


@dataclass(frozen=True, slots=True)
class PackagePolicyResult:
    scanned_files: int
    forbidden_paths: tuple[str, ...]
    secret_assignment_files: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.forbidden_paths and not self.secret_assignment_files


def _forbidden_relative_path(relative: Path) -> bool:
    lowered_parts = {part.casefold() for part in relative.parts}
    if lowered_parts & FORBIDDEN_DIRECTORY_NAMES:
        return True
    name = relative.name.casefold()
    if name == ".env":
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    return relative.suffix.casefold() in FORBIDDEN_SUFFIXES


def verify_package_tree(root: Path) -> PackagePolicyResult:
    scanned = 0
    forbidden: list[str] = []
    secrets: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        scanned += 1
        relative = path.relative_to(root)
        if _forbidden_relative_path(relative):
            forbidden.append(str(relative))
            continue
        if path.stat().st_size > 10_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if SECRET_ASSIGNMENT.search(content):
            secrets.append(str(relative))
    return PackagePolicyResult(
        scanned_files=scanned,
        forbidden_paths=tuple(forbidden),
        secret_assignment_files=tuple(secrets),
    )


__all__ = ["PackagePolicyResult", "verify_package_tree"]
