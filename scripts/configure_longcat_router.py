from __future__ import annotations

import argparse
import re
from getpass import getpass
from pathlib import Path

from router.config import router_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def quote_env(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def upsert(
    lines: list[str],
    key: str,
    value: str,
) -> list[str]:
    pattern = re.compile(
        rf"^\s*{re.escape(key)}\s*="
    )
    replacement = f"{key}={quote_env(value)}"

    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = replacement
            return lines

    lines.append(replacement)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
    )
    args = parser.parse_args()

    if router_settings.longcat_ready and not args.force:
        print("LongCat Router 配置已经存在。")
        print("API Key 未显示。")
        return

    api_key = getpass(
        "请输入 LongCat API Key（输入内容隐藏）："
    ).strip()

    if not api_key:
        raise RuntimeError("API Key 不能为空")

    if ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8")
        lines = text.splitlines()
    else:
        lines = []

    values = {
        "OPC_LONGCAT_API_KEY": api_key,
        "OPC_LONGCAT_BASE_URL": (
            "https://api.longcat.chat/openai/v1"
        ),
        "OPC_LONGCAT_MODEL": "LongCat-2.0",
    }

    for key, value in values.items():
        lines = upsert(lines, key, value)

    ENV_PATH.write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )

    print("LongCat Router 配置已写入 .env。")
    print("API Key 未显示。")


if __name__ == "__main__":
    main()