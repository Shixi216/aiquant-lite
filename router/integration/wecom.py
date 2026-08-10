from __future__ import annotations

import math
import os
import re
from collections import OrderedDict
from typing import Any

from router.integration.schemas import (
    WeComMessageType,
    WeComSimulationRequest,
    WeComSimulationResult,
)
from router.integration.skills import SKILL_REGISTRY, SkillRegistry
from router.integration.sanitization import sanitize_error


_HIDDEN_REASONING = re.compile(
    r"(?is)<(?:analysis|reasoning)>.*?</(?:analysis|reasoning)>|"
    r"\b(?:chain[-_ ]of[-_ ]thought|hidden reasoning)\b.*"
)
_SENSITIVE_JSON_KEY = re.compile(
    r'(?i)"[^"]*(?:token|secret|password|api[_-]?key)[^"]*"\s*:\s*"[^"]*"'
)


def _configured() -> bool:
    channel = bool((os.getenv("WECOM_HOME_CHANNEL") or "").strip())
    credential = any(
        bool((os.getenv(name) or "").strip())
        for name in ("WECOM_SECRET", "WECOM_WEBHOOK", "WECOM_ACCESS_TOKEN")
    )
    return channel and credential


def _safe_text(value: str) -> str:
    cleaned = _HIDDEN_REASONING.sub("[INTERNAL_REASONING_REMOVED]", value)
    cleaned = _SENSITIVE_JSON_KEY.sub('"credential":"[REDACTED]"', cleaned)
    return sanitize_error(cleaned)


def _render_item(item: dict[str, Any], index: int) -> str:
    symbol = str(item.get("symbol") or "").upper()
    name = str(item.get("short_name") or item.get("name") or "")
    rank = item.get("rank", index)
    risk = item.get("risk_flags") or []
    risk_text = ",".join(str(flag) for flag in risk[:3]) or "none"
    return _safe_text(
        f"{rank}. {symbol} {name} | risk={risk_text} | research priority only"
    )


class LocalWeComAdapter:
    """Local renderer only. It never opens a network connection or sends a message."""

    def __init__(
        self,
        *,
        maximum_cached_messages: int = 1000,
        skill_registry: SkillRegistry = SKILL_REGISTRY,
    ) -> None:
        self.maximum_cached_messages = maximum_cached_messages
        self.skill_registry = skill_registry
        self._processed: OrderedDict[str, WeComSimulationResult] = OrderedDict()

    def simulate(self, request: WeComSimulationRequest) -> WeComSimulationResult:
        existing = self._processed.get(request.message_id)
        if existing is not None:
            self._processed.move_to_end(request.message_id)
            return existing.model_copy(update={"duplicate": True})

        total_items = len(request.items)
        total_pages = max(1, math.ceil(total_items / request.page_size))
        page = min(request.page, total_pages)
        start = (page - 1) * request.page_size
        selected = request.items[start : start + request.page_size]
        header = {
            WeComMessageType.CANDIDATE_LIST: "候选列表（研究优先级，不构成买卖建议）",
            WeComMessageType.CANDIDATE_DETAIL: "候选详情（仅研究用途）",
            WeComMessageType.RESEARCH_SUMMARY: "五维研究摘要",
            WeComMessageType.DECISION_SUMMARY: "正式决策摘要（需二次确认）",
            WeComMessageType.MISSING_DATA: "数据缺口",
            WeComMessageType.VETO: "硬风险 VETO",
            WeComMessageType.STATUS: "系统状态",
            WeComMessageType.EXPERIMENT_SUMMARY: "实验摘要（不能证明稳定盈利）",
            WeComMessageType.PARSE_CONFIRMATION: "筛选条件确认",
            WeComMessageType.TEXT_QUERY: "文本请求",
        }[request.message_type]
        lines = [header, _safe_text(request.text)]
        lines.extend(
            _render_item(item, start + index + 1)
            for index, item in enumerate(selected)
        )
        if total_items:
            lines.append(f"page={page}/{total_pages}; total={total_items}")
        requires_confirmation = (
            request.message_type == WeComMessageType.DECISION_SUMMARY
            and request.decision_confirmation != "CONFIRM_DECISION"
        )
        if requires_confirmation:
            lines.append("请在项目内显式确认后再运行 DECISION；不会生成真实订单。")

        result = WeComSimulationResult(
            configuration_status=(
                "CONFIGURED" if _configured() else "NOT_CONFIGURED"
            ),
            message_id=request.message_id,
            duplicate=False,
            message_type=request.message_type,
            rendered_text="\n".join(lines),
            page=page,
            page_size=request.page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            requires_second_confirmation=requires_confirmation,
        )
        self._processed[request.message_id] = result
        self._processed.move_to_end(request.message_id)
        while len(self._processed) > self.maximum_cached_messages:
            self._processed.popitem(last=False)
        return result

    def clear_for_test(self) -> None:
        self._processed.clear()


__all__ = ["LocalWeComAdapter"]
