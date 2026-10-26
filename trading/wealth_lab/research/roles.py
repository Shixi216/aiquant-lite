"""三模型固定角色映射（v3.3）。

model_id 一律来自已验证配置，不做任何猜测：
- RESEARCHER/REVIEWER 使用 router_settings 的现值（.env 已配置且历史真实调用成功）；
- PLANNER（DeepSeek V4 Flash）需要显式配置 OPC_DEEPSEEK_FLASH_MODEL 才启用；
  未配置时 available=False，编排器记录 MODEL_UNAVAILABLE 并停止/降级对应阶段。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RoleSpec:
    role: str
    display_model_name: str
    provider: str
    actual_model_id: str | None
    config_source: str
    available: bool
    note: str = ""


def _planner_spec() -> RoleSpec:
    flash = os.environ.get("OPC_DEEPSEEK_FLASH_MODEL", "").strip()
    if not flash:
        try:  # 服务进程可能未显式导出 .env
            from dotenv import dotenv_values
            from trading.wealth_lab.config import PROJECT_ROOT
            flash = (dotenv_values(PROJECT_ROOT / ".env").get("OPC_DEEPSEEK_FLASH_MODEL") or "").strip()
        except Exception:
            flash = ""
    if flash:
        return RoleSpec(
            role="PLANNER", display_model_name="DeepSeek V4 Flash",
            provider="deepseek", actual_model_id=flash,
            config_source="env:OPC_DEEPSEEK_FLASH_MODEL", available=True,
        )
    return RoleSpec(
        role="PLANNER", display_model_name="DeepSeek V4 Flash",
        provider="deepseek", actual_model_id=None,
        config_source="env:OPC_DEEPSEEK_FLASH_MODEL (未配置)",
        available=False,
        note="未配置 OPC_DEEPSEEK_FLASH_MODEL；禁止猜测 model_id，"
             "按 MODEL_UNAVAILABLE 处理（可经任务中心/编排器降级）",
    )


def build_role_specs() -> dict[str, RoleSpec]:
    from router.config import router_settings
    specs = {
        "RESEARCHER": RoleSpec(
            role="RESEARCHER", display_model_name="Qwen 3.8 Max 0902",
            provider="qwen", actual_model_id=router_settings.qwen_model,
            config_source="router_settings.qwen_model（registry 历史真实调用验证）",
            available=router_settings.qwen_ready,
        ),
        "REVIEWER": RoleSpec(
            role="REVIEWER", display_model_name="DeepSeek V4 Pro",
            provider="deepseek", actual_model_id=router_settings.deepseek_model,
            config_source="router_settings.deepseek_model（v3.1 起真实调用验证）",
            available=router_settings.deepseek_ready,
        ),
        "PLANNER": _planner_spec(),
    }
    return specs


ROLE_SPECS = build_role_specs()


def experimental_ai_enabled() -> bool:
    """EXPERIMENTAL_AI_RESEARCH Feature Flag（默认关闭）。"""
    flag = os.environ.get("WEALTH_LAB_EXPERIMENTAL_AI", "").strip()
    if not flag:
        try:
            from dotenv import dotenv_values
            from trading.wealth_lab.config import PROJECT_ROOT
            flag = (dotenv_values(PROJECT_ROOT / ".env").get("WEALTH_LAB_EXPERIMENTAL_AI") or "").strip()
        except Exception:
            flag = ""
    return flag.lower() in ("1", "true", "yes")
