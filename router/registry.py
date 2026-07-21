from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from router.config import router_settings


class RoleSpec(BaseModel):
    """Logical specialist role handled by the Agent Router."""

    model_config = ConfigDict(extra="forbid")

    role: str
    display_name: str
    provider: str
    preferred_model: str
    task_type: str
    description: str
    enabled: bool = False


ROLE_SPECS: dict[str, RoleSpec] = {
    "announcement_verifier": RoleSpec(
        role="announcement_verifier",
        display_name="公告核验 Agent",
        provider="qwen",
        preferred_model="Qwen3.7-Plus",
        task_type="text_verification",
        description="核对公告原文、日期、主体和关键事实。",
    ),
    "adversarial_reviewer": RoleSpec(
        role="adversarial_reviewer",
        display_name="对抗审查 Agent",
        provider="hy3",
        preferred_model="Hy3",
        task_type="adversarial_review",
        description="寻找研究结论中的反例、漏洞和证据缺口。",
    ),
    "vision_reader": RoleSpec(
        role="vision_reader",
        display_name="视觉读取 Agent",
        provider="mimo",
        preferred_model="MiMo V2.5",
        task_type="vision",
        description="读取普通图表、截图和财务报告页面。",
    ),
    "risk_controller": RoleSpec(
        role="risk_controller",
        display_name="风险控制 Agent",
        provider="deepseek",
        preferred_model="DeepSeek V4 Pro",
        task_type="risk_analysis",
        description="识别风险暴露、约束条件和否决项。",
    ),
    "complex_vision": RoleSpec(
        role="complex_vision",
        display_name="复杂视觉 Agent",
        provider="mimo",
        preferred_model="MiMo V2.5 Pro",
        task_type="complex_vision",
        description="处理复杂图表、跨页表格和高难度视觉材料。",
    ),
    "news_processor": RoleSpec(
        role="news_processor",
        display_name="新闻处理 Agent",
        provider="longcat",
        preferred_model="LongCat-2.0",
        task_type="bulk_text",
        description="批量整理新闻、去重、聚类和生成摘要。",
    ),
}


def _role_enabled(spec: RoleSpec) -> bool:
    if spec.role == "news_processor":
        return router_settings.longcat_ready

    return False


def list_roles() -> list[RoleSpec]:
    return [
        ROLE_SPECS[role].model_copy(
            update={
                "enabled": _role_enabled(ROLE_SPECS[role]),
            }
        )
        for role in sorted(ROLE_SPECS)
    ]


def get_role(role: str) -> RoleSpec | None:
    spec = ROLE_SPECS.get(role)

    if spec is None:
        return None

    return spec.model_copy(
        update={"enabled": _role_enabled(spec)}
    )