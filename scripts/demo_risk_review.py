"""演示：deepseek-v4-pro 风控复核（risk_controller）

模拟场景：五维分析给出"买入"建议，deepseek 做对抗性审查。
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "E:/hermes-opc")


async def demo():
    from router.registry import get_role
    from router.services.role_handler_registry import get_role_handler
    from router.services.provider_registry import get_model_provider
    from router.services.risk_controller_handler import build_risk_review_prompt, normalize_risk_review_output

    # 模拟五维分析输出（建议买入）
    original_output = (
        "【综合决策】60/40综合分: +0.60（>=0.3 偏多）\n"
        "建议: 关注/逢低分批建仓\n"
        "技术面: +0.78（突破20日均线，MACD金叉）\n"
        "基本面: +0.33（ROE回升，负债率下降）\n"
        "资金面: +0.60（价涨量增）\n"
        "情绪面: +0.13\n"
        "政策面: +0.30（业绩预增公告）\n"
        "主要风险: 短期涨幅较大（年初至今+35%），需注意回调"
    )

    risk_prompt = build_risk_review_prompt(
        original_role="decision_support",
        symbol="600509.SH",
        risk_level="high",
        original_prompt="分析天富能源600509，给出操作建议",
        original_output=original_output,
    )

    print("=" * 60)
    print("deepseek-v4-pro 风控复核演示（risk_controller）")
    print("=" * 60)
    print("\n【被审查的输出】")
    print(original_output)

    # 获取 risk_controller 角色配置
    risk_role = get_role("risk_controller")
    if risk_role is None:
        print("\n❌ risk_controller 角色未配置")
        return
    print(f"\n角色: {risk_role.role} | provider: {risk_role.provider} | model: {risk_role.preferred_model}")

    provider = get_model_provider(risk_role.provider)
    if provider is None:
        print("\n❌ deepseek provider 不可用")
        return

    print("\n【风控复核进行中...】")
    try:
        # 获取系统提示词
        handler = get_role_handler(risk_role.role)
        system_prompt = None
        if handler is not None and hasattr(handler, "build_system_prompt"):
            system_prompt = handler.build_system_prompt()
        if system_prompt is None:
            system_prompt = (
                "你是风险控制复核员。审查给定的投资分析输出，找出遗漏风险、逻辑错误、"
                "数据矛盾。必须返回JSON: {\"decision\": \"approve\"|\"revise\"|\"reject\", "
                "\"assessed_risk_level\": \"low\"|\"medium\"|\"high\"|\"critical\", "
                "\"findings\": [问题列表], \"required_actions\": [整改项], \"confidence\": 0-1}"
            )

        response = await provider.invoke(
            role=risk_role.role,
            prompt=risk_prompt,
            system_prompt=system_prompt,
            temperature=0.3,     # 低温度：风控要严谨
            max_tokens=1500,
        )
        content = response.content if hasattr(response, "content") else str(response)
        print("\n【deepseek 原始输出】")
        print(content[:700])

        # 解析验证
        decision, output = normalize_risk_review_output(content)
        print("\n【风控复核结果（结构化）】")
        print(f"  决策: {decision}  (approve=批准 / revise=修改 / reject=否决)")
        print(f"  评估风险等级: {output.assessed_risk_level}")
        print(f"  置信度: {output.confidence}")
        print(f"  发现问题 ({len(output.findings)}):")
        for f in output.findings:
            print(f"    ⚠️ {f}")
        print(f"  要求整改 ({len(output.required_actions)}):")
        for a in output.required_actions:
            print(f"    📋 {a}")

        if decision == "reject":
            print("\n💡 结论: deepseek 否决了买入建议（对抗性复核生效）")
        elif decision == "revise":
            print("\n💡 结论: deepseek 要求修改建议后再执行")
        else:
            print("\n💡 结论: deepseek 批准建议（无重大遗漏风险）")
    except Exception as exc:
        print(f"❌ 调用失败: {type(exc).__name__}: {str(exc)[:300]}")


if __name__ == "__main__":
    asyncio.run(demo())
