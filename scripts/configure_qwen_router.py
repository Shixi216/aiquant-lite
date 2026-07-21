from __future__ import annotations

import re
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def quote_env(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def set_env_value(
    lines: list[str],
    key: str,
    value: str,
) -> list[str]:
    pattern = re.compile(
        rf"^\s*{re.escape(key)}\s*="
    )

    filtered = [
        line
        for line in lines
        if not pattern.match(line)
    ]

    filtered.append(
        f"{key}={quote_env(value)}"
    )

    return filtered


def validate_api_key(value: str) -> str:
    key = value.strip()

    if len(key) < 20:
        raise RuntimeError(
            "Qwen API Key 长度异常"
        )

    if key.lower().startswith("bearer "):
        raise RuntimeError(
            "不要输入 Bearer 前缀"
        )

    if any(character.isspace() for character in key):
        raise RuntimeError(
            "API Key 包含空格或换行"
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
            "检测到 API Key 重复拼接"
        )

    return key


def validate_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise RuntimeError(
            "Qwen Base URL 必须使用 HTTPS"
        )

    if not hostname.endswith(".aliyuncs.com"):
        raise RuntimeError(
            "Qwen Base URL 域名不是 aliyuncs.com"
        )

    if not parsed.path.endswith(
        "/compatible-mode/v1"
    ):
        raise RuntimeError(
            "Qwen Base URL 必须以 "
            "/compatible-mode/v1 结尾"
        )

    if parsed.query or parsed.fragment:
        raise RuntimeError(
            "Qwen Base URL 不应包含查询参数或片段"
        )

    return normalized


def main() -> None:
    print("请从阿里云百炼控制台复制 OpenAI 兼容 Base URL。")
    print("不要填写 /chat/completions 后缀。")

    base_url = validate_base_url(
        input("Qwen Base URL：")
    )

    first = validate_api_key(
        getpass(
            "请输入 Qwen API Key（输入隐藏）："
        )
    )

    second = validate_api_key(
        getpass(
            "请再次输入 Qwen API Key（输入隐藏）："
        )
    )

    if first != second:
        raise RuntimeError(
            "两次输入的 API Key 不一致"
        )

    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
    else:
        lines = []

    values = {
        "OPC_QWEN_API_KEY": first,
        "OPC_QWEN_BASE_URL": base_url,
        "OPC_QWEN_MODEL": "qwen3.7-plus",
    }

    for name, value in values.items():
        lines = set_env_value(
            lines,
            name,
            value,
        )

    temporary_path = ENV_PATH.with_name(
        ".env.qwen.tmp"
    )

    temporary_path.write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )

    temporary_path.replace(ENV_PATH)

    print("Qwen Router 配置已写入 .env。")
    print("模型：qwen3.7-plus")
    print("API Key 未显示。")


if __name__ == "__main__":
    main()