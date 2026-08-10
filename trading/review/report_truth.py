"""Formal-report truth gate bound to a DecisionEngine response.

This module validates research output only.  It cannot create orders or mutate
positions, decisions, frozen zones, or production parameters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_response import DecisionResponse
from trading.decision_support.execution_status import compute_execution_status


@dataclass(frozen=True)
class ReportIndicator:
    name: str
    value: float | None
    source_record_id: str | None
    estimated: bool = False


@dataclass(frozen=True)
class RiskEventStatement:
    event: str
    impact: str


@dataclass(frozen=True)
class TargetPriceStatement:
    target_price: float
    impact: str


@dataclass(frozen=True)
class CapitalFlowStatement:
    source_kind: str
    classification: str


@dataclass(frozen=True)
class FormalReportDraft:
    action: str
    execution_status: str
    current_price: float
    indicators: tuple[ReportIndicator, ...] = ()
    risk_events: tuple[RiskEventStatement, ...] = ()
    target_prices: tuple[TargetPriceStatement, ...] = ()
    capital_flows: tuple[CapitalFlowStatement, ...] = ()
    severe_missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class TruthIssue:
    code: str
    message: str
    blocking: bool = True


@dataclass(frozen=True)
class TruthValidationResult:
    issues: tuple[TruthIssue, ...] = ()

    @property
    def formal_report_allowed(self) -> bool:
        return not any(issue.blocking for issue in self.issues)

    @property
    def passed(self) -> bool:
        return not self.issues


class FormalReportBlockedError(ValueError):
    pass


class ReportTruthValidator:
    _NEUTRAL = {"NEUTRAL", "中性"}
    _POSITIVE = {"POSITIVE", "BULLISH", "利好", "正面"}
    _NO_RISK = {"NO_RISK_EVENT", "NONE", "无风险事件", "暂无风险事件"}
    _MARGIN = {"MARGIN_FINANCING", "MARGIN", "两融", "融资融券"}
    _MAIN_FORCE = {"MAIN_FORCE", "主力资金", "主力"}

    @staticmethod
    def _action_value(response: DecisionResponse) -> str:
        return (
            response.action.value
            if hasattr(response.action, "value")
            else str(response.action)
        )

    def validate(
        self,
        response: DecisionResponse,
        draft: FormalReportDraft,
    ) -> TruthValidationResult:
        issues: list[TruthIssue] = []
        expected_action = self._action_value(response)
        if draft.action != expected_action:
            issues.append(
                TruthIssue(
                    "ACTION_MISMATCH",
                    f"报告动作 {draft.action} 不等于 DecisionEngine 动作 {expected_action}",
                )
            )

        expected_status, _ = compute_execution_status(
            current_price=draft.current_price,
            action=expected_action,
            veto_triggered=response.veto_triggered,
            frozen_entry_zone=list(response.frozen_entry_zone),
            frozen_preferred_zone=list(response.frozen_preferred_zone),
            frozen_stop_loss_price=float(response.frozen_stop_loss_price or 0),
        )
        if draft.execution_status != expected_status:
            issues.append(
                TruthIssue(
                    "EXECUTION_STATUS_NOT_FROZEN",
                    "报告执行状态未按 DecisionEngine 冻结区间重新计算",
                )
            )

        for indicator in draft.indicators:
            normalized = indicator.name.upper().replace("_", "")
            is_protected = normalized.startswith(("RSI", "ATR", "MA", "SMA", "EMA"))
            if is_protected and (
                indicator.estimated
                or indicator.value is None
                or not indicator.source_record_id
            ):
                issues.append(
                    TruthIssue(
                        "INDICATOR_NOT_MEASURED",
                        f"{indicator.name} 必须来自可追溯实测值，禁止估算",
                    )
                )

        for statement in draft.risk_events:
            if (
                statement.event.strip().upper() in self._NO_RISK
                or statement.event.strip() in self._NO_RISK
            ) and statement.impact.strip().upper() not in self._NEUTRAL:
                issues.append(
                    TruthIssue(
                        "NO_RISK_EVENT_NOT_NEUTRAL",
                        "无风险事件只能记为中性",
                    )
                )

        for statement in draft.target_prices:
            if (
                statement.target_price < draft.current_price
                and (
                    statement.impact.strip().upper() in self._POSITIVE
                    or statement.impact.strip() in self._POSITIVE
                )
            ):
                issues.append(
                    TruthIssue(
                        "LOW_TARGET_MARKED_POSITIVE",
                        "目标价低于现价，不得标记为利好",
                    )
                )

        for statement in draft.capital_flows:
            source = statement.source_kind.strip()
            classification = statement.classification.strip()
            if (
                (source.upper() in self._MARGIN or source in self._MARGIN)
                and (
                    classification.upper() in self._MAIN_FORCE
                    or classification in self._MAIN_FORCE
                )
            ):
                issues.append(
                    TruthIssue(
                        "MARGIN_MISLABELED_MAIN_FORCE",
                        "两融资金不得写成主力资金",
                    )
                )

        if response.data_status in {DataStatus.STALE, DataStatus.FAILED}:
            issues.append(
                TruthIssue(
                    "FORMAL_DATA_UNAVAILABLE",
                    f"数据状态为 {response.data_status.value}，停止正式报告",
                )
            )
        if draft.severe_missing_fields:
            issues.append(
                TruthIssue(
                    "SEVERE_DATA_MISSING",
                    "严重缺失字段：" + ", ".join(draft.severe_missing_fields),
                )
            )
        return TruthValidationResult(tuple(issues))

    def validate_or_raise(
        self,
        response: DecisionResponse,
        draft: FormalReportDraft,
    ) -> TruthValidationResult:
        result = self.validate(response, draft)
        if not result.formal_report_allowed:
            codes = ", ".join(issue.code for issue in result.issues)
            raise FormalReportBlockedError(f"正式报告已阻断：{codes}")
        return result

    def render_validated(
        self,
        response: DecisionResponse,
        draft: FormalReportDraft,
        renderer: Callable[[FormalReportDraft], str],
    ) -> str:
        """The renderer is called only after every truth check passes."""

        self.validate_or_raise(response, draft)
        return renderer(draft)


__all__ = [
    "CapitalFlowStatement",
    "FormalReportBlockedError",
    "FormalReportDraft",
    "ReportIndicator",
    "ReportTruthValidator",
    "RiskEventStatement",
    "TargetPriceStatement",
    "TruthIssue",
    "TruthValidationResult",
]
