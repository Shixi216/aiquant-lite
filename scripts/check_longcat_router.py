from __future__ import annotations

import json
from typing import Any

import httpx


BASE_URL = "http://127.0.0.1:8765"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_model_json(content: str) -> dict[str, Any]:
    require(bool(content.strip()), "模型返回内容为空")
    require("```" not in content, "模型返回内容包含代码围栏")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "模型返回内容不是合法 JSON："
            f"{exc}; content={content[:500]}"
        ) from exc

    require(
        isinstance(parsed, dict),
        "模型顶层输出必须是 JSON 对象",
    )

    return parsed


def main() -> None:
    with httpx.Client(
        base_url=BASE_URL,
        timeout=120,
        trust_env=False,
    ) as client:
        roles_response = client.get("/v1/roles")
        roles_response.raise_for_status()
        roles_data = roles_response.json()

        roles = {
            item["role"]: item
            for item in roles_data.get("roles", [])
        }

        news_role = roles.get("news_processor")

        print("角色启用检查：")
        print(f"启用角色数：{roles_data.get('enabled_count')}")
        print(
            "news_processor enabled："
            f"{news_role.get('enabled') if news_role else None}"
        )

        require(
            news_role is not None,
            "未找到 news_processor 角色",
        )
        require(
            news_role.get("enabled") is True,
            "news_processor 尚未启用",
        )

        prompt = """
请处理下面这条内部接口测试记录。

上游元数据：
source_name=测试财经媒体
source_level=media
verified=false
published_at=2026-07-16T10:30:00+08:00

原始文本：
测试财经媒体报道称，某券商将黄河旋风的评级由“中性”
上调至“增持”，并给出15元目标价。该信息目前未见公司
官方公告确认。

要求：
按照系统定义的 JSON 结构输出。由于上游来源是媒体且
verified=false，本记录不得标记为 FACT。
""".strip()

        response = client.post(
            "/v1/invoke",
            json={
                "role": "news_processor",
                "prompt": prompt,
                "temperature": 0,
                "max_tokens": 1200,
            },
        )

        data = response.json()

        print("\nLongCat 调用检查：")
        print(f"状态码：{response.status_code}")

        require(
            response.status_code == 200,
            f"LongCat 调用失败：{data}",
        )
        require(
            data.get("role") == "news_processor",
            "返回角色错误",
        )
        require(
            data.get("provider") == "longcat",
            "返回 Provider 错误",
        )

        require(
            data.get("validated") is True,
            "Router 未确认结构校验通过",
        )

        attempts = data.get("attempts")

        require(
            attempts in {1, 2},
            f"异常调用次数：{attempts}",
        )

        print(f"结构校验：{data.get('validated')}")
        print(f"调用次数：{attempts}")
        content = str(data.get("content") or "")
        parsed = parse_model_json(content)

        items = parsed.get("items")
        clusters = parsed.get("clusters")
        summary = parsed.get("summary")

        require(
            isinstance(items, list) and bool(items),
            "JSON 中缺少有效 items",
        )
        require(
            isinstance(clusters, list),
            "JSON 中缺少 clusters 数组",
        )
        require(
            isinstance(summary, dict),
            "JSON 中缺少 summary 对象",
        )

        first_item = items[0]

        require(
            isinstance(first_item, dict),
            "items[0] 必须是对象",
        )
        require(
            first_item.get("source_class")
            == "MEDIA_STATEMENT",
            "媒体记录被错误标记为 FACT",
        )

        signals = first_item.get("market_signals")
        require(
            isinstance(signals, list),
            "market_signals 必须是数组",
        )

        signal_types = {
            signal.get("type")
            for signal in signals
            if isinstance(signal, dict)
        }

        require(
            bool(
                signal_types.intersection(
                    {"rating_change", "target_price"}
                )
            ),
            "未提取评级或目标价信号",
        )

        print(f"角色：{data.get('role')}")
        print(f"Provider：{data.get('provider')}")
        print(f"模型：{data.get('model')}")
        print(f"耗时：{data.get('latency_ms')}ms")
        print(f"来源分类：{first_item.get('source_class')}")
        print(f"信号类型：{sorted(signal_types)}")
        print(f"Token 用量：{data.get('usage')}")

        print("\n结构化结果：")
        print(
            json.dumps(
                parsed,
                ensure_ascii=False,
                indent=2,
            )
        )

    print("\nLongCat news_processor 角色检查通过")


if __name__ == "__main__":
    main()