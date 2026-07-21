from __future__ import annotations

import json

import httpx


TASK_ID = "task_277f1da11cb3477294797307b1e2c339"

AUDIT_URL = (
    "http://127.0.0.1:8765"
    f"/v1/audit/tasks/{TASK_ID}"
)

MOJIBAKE_MARKERS = (
    "è¯·",
    "é»",
    "å¹´",
    "æ¶",
    "ç»",
)


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def unicode_escape(
    value: str,
    limit: int = 100,
) -> str:
    return (
        value[:limit]
        .encode(
            "unicode_escape"
        )
        .decode("ascii")
    )


def cjk_count(value: str) -> int:
    return sum(
        1
        for char in value
        if "\u4e00" <= char <= "\u9fff"
    )


def main() -> None:
    with httpx.Client(
        timeout=30,
        trust_env=False,
    ) as client:
        response = client.get(AUDIT_URL)

    response.raise_for_status()

    raw_bytes = response.content

    decoded_utf8 = raw_bytes.decode(
        "utf-8",
        errors="strict",
    )

    payload = response.json()

    request_json = payload.get(
        "request_json"
    )

    require(
        isinstance(request_json, dict),
        "审计响应缺少 request_json",
    )

    prompt = request_json.get("prompt")

    require(
        isinstance(prompt, str),
        "审计请求缺少字符串 prompt",
    )

    agent_results = payload.get(
        "agent_results"
    )

    require(
        isinstance(agent_results, list)
        and agent_results,
        "审计响应缺少 agent_results",
    )

    result_json = agent_results[0].get(
        "result_json"
    )

    require(
        isinstance(result_json, dict),
        "Agent 结果缺少 result_json",
    )

    result_text = json.dumps(
        result_json,
        ensure_ascii=False,
    )

    full_text = json.dumps(
        payload,
        ensure_ascii=False,
    )

    marker_hits = {
        marker: full_text.count(marker)
        for marker in MOJIBAKE_MARKERS
    }

    print("Audit UTF-8 integrity:")
    print(
        "content_type="
        f"{response.headers.get('content-type')}"
    )
    print(f"raw_byte_count={len(raw_bytes)}")
    print(
        "strict_utf8_decode=True"
    )
    print(
        "response_json_parse=True"
    )

    print("\nPrompt:")
    print(
        f"cjk_count={cjk_count(prompt)}"
    )
    print(
        "contains_chinese_instruction="
        f"{'请' in prompt}"
    )
    print(
        "contains_company_name="
        f"{'黄河旋风' in prompt}"
    )
    print(
        "prefix_unicode_escape="
        f"{unicode_escape(prompt)}"
    )

    print("\nAgent result:")
    print(
        "result_cjk_count="
        f"{cjk_count(result_text)}"
    )
    print(
        "contains_company_name="
        f"{'黄河旋风' in result_text}"
    )
    print(
        "result_prefix_unicode_escape="
        f"{unicode_escape(result_text)}"
    )

    print("\nMojibake markers:")
    print(marker_hits)

    require(
        "请" in decoded_utf8,
        "原始 HTTP 响应按 UTF-8 解码后仍缺少中文",
    )

    require(
        "请" in prompt,
        "审计数据库中的 Prompt 已发生乱码",
    )

    require(
        "黄河旋风" in full_text,
        "审计响应中没有正确的公司中文名称",
    )

    require(
        cjk_count(prompt) > 50,
        "Prompt 中的有效中文字符数量异常",
    )

    require(
        cjk_count(result_text) > 10,
        "Agent 结果中的有效中文字符数量异常",
    )

    require(
        not any(marker_hits.values()),
        (
            "Python 客户端读取结果仍存在乱码标记："
            f"{marker_hits}"
        ),
    )

    print(
        "\nAudit UTF-8 integrity checks passed"
    )


if __name__ == "__main__":
    main()