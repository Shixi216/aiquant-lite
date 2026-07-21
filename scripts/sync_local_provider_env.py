"""Safely sync an allowlist of provider settings into the project .env.

Values are never printed. Messaging credentials and unused provider keys are
intentionally outside the allowlist. The command is dry-run unless ``--apply``
is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


SYNC_KEYS = (
    "TUSHARE_TOKEN",
    "TAVILY_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "XIAOMI_API_KEY",
    "XIAOMI_BASE_URL",
    "LONGCAT_API_KEY",
    "LONGCAT_BASE_URL",
)

FORBIDDEN_PREFIXES = ("QQ", "WECOM", "TOKENHUB")


class EnvSyncError(RuntimeError):
    """Raised when local environment files cannot be synchronized safely."""


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in values:
            raise EnvSyncError(f"duplicate dotenv key: {key}")
        values[key] = value.strip()
    return values


def patch_dotenv(text: str, updates: dict[str, str]) -> str:
    forbidden = [key for key in updates if key.startswith(FORBIDDEN_PREFIXES)]
    if forbidden:
        raise EnvSyncError("messaging or unused provider credentials are not allowed")

    lines = text.splitlines()
    indexes: dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in indexes:
            raise EnvSyncError(f"duplicate target dotenv key: {key}")
        indexes[key] = index

    for key in SYNC_KEYS:
        value = updates.get(key)
        if not value:
            continue
        replacement = f"{key}={value}"
        if key in indexes:
            lines[indexes[key]] = replacement
        else:
            lines.append(replacement)
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def sync(source: Path, target: Path, *, apply: bool) -> dict[str, object]:
    if not source.is_file():
        raise EnvSyncError("source dotenv file does not exist")
    source_values = parse_dotenv(source.read_text(encoding="utf-8"))
    selected = {
        key: source_values[key]
        for key in SYNC_KEYS
        if source_values.get(key)
    }
    target_text = target.read_text(encoding="utf-8") if target.is_file() else ""
    patched = patch_dotenv(target_text, selected)

    summary: dict[str, object] = {
        "mode": "apply" if apply else "dry-run",
        "source_keys_found": sorted(selected),
        "source_keys_missing": sorted(set(SYNC_KEYS) - set(selected)),
        "values_hidden": True,
        "messaging_credentials_copied": False,
    }
    if not apply:
        return summary

    backup: Path | None = None
    if target.is_file():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = target.parent / "backups" / "local-env"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"project-env-{timestamp}.bak"
        shutil.copy2(target, backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, patched)
    summary["backup_created"] = backup is not None
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path(__file__).parents[1] / ".env")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = sync(args.source.resolve(), args.target.resolve(), apply=args.apply)
    except EnvSyncError as exc:
        print(f"Environment not changed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
