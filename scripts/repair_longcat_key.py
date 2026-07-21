from __future__ import annotations

import re
from datetime import datetime
from getpass import getpass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def quote_env(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def validate_key(key: str) -> None:
    if not key:
        raise RuntimeError("API Key 不能为空")

    if len(key) < 20:
        raise RuntimeError("API Key 长度异常，拒绝写入")

    if key.lower().startswith("bearer "):
        raise RuntimeError(
            "不要输入 Bearer 前缀，只输入凭据本身"
        )

    if any(character.isspace() for character in key):
        raise RuntimeError(
            "API Key 中包含空格或换行，拒绝写入"
        )

    if (
        len(key) >= 2
        and key[0] in {'"', "'"}
        and key[-1] == key[0]
    ):
        raise RuntimeError(
            "不要在 API Key 外添加引号"
        )

    repeated_prefix = (
        len(key) >= 40
        and key.find(key[:20], 20) >= 0
    )

    if repeated_prefix:
        raise RuntimeError(
            "检测到凭据内容重复拼接，拒绝写入"
        )


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
    print("请粘贴新生成的 LongCat 凭据。")
    print("只粘贴一次，不要包含 Bearer、引号或空格。")

    first = getpass(
        "请输入新凭据（输入隐藏）："
    ).strip()

    second = getpass(
        "请再次输入新凭据（输入隐藏）："
    ).strip()

    if first != second:
        raise RuntimeError(
            "两次输入不一致，未修改 .env"
        )

    validate_key(first)

    if ENV_PATH.exists():
        original = ENV_PATH.read_text(encoding="utf-8")
        lines = original.splitlines()

        timestamp = datetime.now().strftime(
            "%Y%m%d-%H%M%S"
        )
        backup_path = ENV_PATH.with_name(
            f".env.backup-{timestamp}"
        )
        backup_path.write_text(
            original,
            encoding="utf-8",
        )
    else:
        lines = []
        backup_path = None

    values = {
        "OPC_LONGCAT_API_KEY": first,
        "OPC_LONGCAT_BASE_URL": (
            "https://api.longcat.chat/openai/v1"
        ),
        "OPC_LONGCAT_MODEL": "LongCat-2.0",
    }

    for name, value in values.items():
        lines = upsert(lines, name, value)

    ENV_PATH.write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )

    print("LongCat 凭据已安全替换。")
    print(f"凭据长度：{len(first)}")
    print("凭据内容未显示。")

    if backup_path is not None:
        print(f"原配置备份：{backup_path.name}")


if __name__ == "__main__":
    main()