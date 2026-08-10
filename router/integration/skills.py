from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class SkillModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillCategory(StrEnum):
    SCREENING = "SCREENING"
    RESEARCH = "RESEARCH"
    DECISION = "DECISION"
    WORKSPACE = "WORKSPACE"
    REVIEW = "REVIEW"
    EXPERIMENT = "EXPERIMENT"
    SYSTEM = "SYSTEM"


class SkillInvocation(SkillModel):
    request_id: str = Field(pattern=r"^req_[0-9a-f]{24}$")
    conversation_id: str | None = None
    data_cutoff: datetime
    query: str | None = Field(default=None, max_length=2000)
    symbols: list[str] = Field(default_factory=list, max_length=30)
    payload: dict[str, Any] = Field(default_factory=dict)
    confirmation: str | None = Field(default=None, max_length=256)

    @field_validator("data_cutoff")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_cutoff must include timezone")
        return value

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("symbols must be unique")
        for value in values:
            if not __import__("re").fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", value):
                raise ValueError("invalid A-share symbol")
        return values


class SkillResultCard(SkillModel):
    card_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    status: Literal[
        "SUCCESS",
        "DEGRADED",
        "WAITING_CONFIRMATION",
        "FAILED",
    ]
    request_id: str = Field(pattern=r"^req_[0-9a-f]{24}$")
    data_cutoff: datetime
    fields: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    research_only: Literal[True] = True
    order_created: Literal[False] = False


class SkillContract(SkillModel):
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str
    description: str
    category: SkillCategory
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_modes: list[str]
    read_only: bool
    requires_confirmation: bool
    may_write_research_data: bool
    may_write_manual_ledger: bool
    network_policy: str
    model_policy: str
    timeout: int = Field(ge=1, le=600)
    cancellation_policy: str
    risk_flags: list[str]
    version: str
    enabled: bool


class SkillRegistry:
    def __init__(self, contracts: list[SkillContract]) -> None:
        self._contracts = {item.skill_id: item for item in contracts}
        if len(self._contracts) != len(contracts):
            raise ValueError("duplicate skill_id")

    def list(self, *, enabled_only: bool = True) -> list[SkillContract]:
        return [
            item
            for item in self._contracts.values()
            if item.enabled or not enabled_only
        ]

    def get(self, skill_id: str) -> SkillContract:
        try:
            return self._contracts[skill_id]
        except KeyError as error:
            raise KeyError("unknown skill") from error

    def validate_input(
        self, skill_id: str, payload: dict[str, Any]
    ) -> SkillInvocation:
        contract = self.get(skill_id)
        if not contract.enabled:
            raise ValueError("skill is disabled")
        invocation = SkillInvocation.model_validate(payload)
        if skill_id == "stock_research" and len(invocation.symbols) > 30:
            raise ValueError("stock_research accepts at most 30 symbols")
        if skill_id == "decision_support":
            if len(invocation.symbols) != 1:
                raise ValueError("decision_support requires exactly one symbol")
            expected = f"CONFIRM_DECISION:{invocation.symbols[0]}"
            if invocation.confirmation != expected:
                raise ValueError("decision_support requires exact confirmation")
        if skill_id == "manual_trade_tracking":
            operation = str(invocation.payload.get("operation") or "PREVIEW")
            if operation == "CONFIRM":
                preview_id = str(invocation.payload.get("preview_id") or "")
                expected = f"CONFIRM_MANUAL_TRADE:{preview_id}"
                if not preview_id or invocation.confirmation != expected:
                    raise ValueError("manual trade confirmation is invalid")
            elif operation != "PREVIEW":
                raise ValueError("manual trade operation must be PREVIEW or CONFIRM")
        return invocation

    def validate_output(
        self, skill_id: str, payload: dict[str, Any]
    ) -> SkillResultCard:
        self.get(skill_id)
        return SkillResultCard.model_validate(payload)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return TypeAdapter(model).json_schema()


def _contract(
    skill_id: str,
    name: str,
    description: str,
    category: SkillCategory,
    *,
    modes: list[str],
    read_only: bool = True,
    confirmation: bool = False,
    research_write: bool = False,
    ledger_write: bool = False,
    network: str = "LOCAL_ONLY",
    model: str = "DISABLED",
    timeout: int = 60,
    cancellation: str = "COOPERATIVE",
    risks: list[str] | None = None,
) -> SkillContract:
    return SkillContract(
        skill_id=skill_id,
        name=name,
        description=description,
        category=category,
        input_schema=_schema(SkillInvocation),
        output_schema=_schema(SkillResultCard),
        allowed_modes=modes,
        read_only=read_only,
        requires_confirmation=confirmation,
        may_write_research_data=research_write,
        may_write_manual_ledger=ledger_write,
        network_policy=network,
        model_policy=model,
        timeout=timeout,
        cancellation_policy=cancellation,
        risk_flags=risks or ["RESEARCH_ONLY"],
        version="1.0.0",
        enabled=True,
    )


SKILL_REGISTRY = SkillRegistry(
    [
        _contract(
            "market_scanner",
            "全市场扫描",
            "本地确定性扫描，网络和模型调用均为0。",
            SkillCategory.SCREENING,
            modes=["SCREENING"],
            timeout=15,
            risks=["NOT_A_TRADE_RECOMMENDATION"],
        ),
        _contract(
            "scanner_plan_modification",
            "筛选计划增量修改",
            "仅修改用户明确指定的既有查询字段并输出差异。",
            SkillCategory.SCREENING,
            modes=["SCREENING"],
            risks=["NOT_A_TRADE_RECOMMENDATION"],
        ),
        _contract(
            "stock_research",
            "个股研究",
            "研究最多30只候选，AI深度分析默认最多10只。",
            SkillCategory.RESEARCH,
            modes=["RESEARCH"],
            research_write=True,
            confirmation=True,
            timeout=120,
        ),
        _contract(
            "stock_comparison",
            "个股比较",
            "在同一时间截面比较结构化研究结果。",
            SkillCategory.RESEARCH,
            modes=["RESEARCH"],
        ),
        _contract(
            "announcement_verification",
            "公告核验",
            "核验上市公司公告及来源证据。",
            SkillCategory.RESEARCH,
            modes=["RESEARCH"],
            network="BOUNDED_READ_ONLY",
            timeout=120,
        ),
        _contract(
            "news_impact_analysis",
            "消息影响分析",
            "分析经核验消息的研究影响。",
            SkillCategory.RESEARCH,
            modes=["RESEARCH"],
            network="BOUNDED_READ_ONLY",
            model="OPTIONAL_BOUNDED",
            timeout=120,
        ),
        _contract(
            "decision_support",
            "正式决策支持",
            "单只股票、显式二次确认；不会创建订单。",
            SkillCategory.DECISION,
            modes=["DECISION"],
            confirmation=True,
            risks=["HARD_VETO_FINAL", "NO_ORDER_CREATED"],
        ),
        _contract(
            "watchlist_management",
            "自选股管理",
            "管理本地研究观察列表。",
            SkillCategory.WORKSPACE,
            modes=["RESEARCH"],
            read_only=False,
            research_write=True,
        ),
        _contract(
            "position_risk_review",
            "持仓风险复核",
            "只读复核人工持仓风险，不执行交易。",
            SkillCategory.REVIEW,
            modes=["RESEARCH"],
            risks=["NO_TRADE_EXECUTION"],
        ),
        _contract(
            "backtest",
            "回测",
            "研究回测，不写实盘、人工账本或Paper成交。",
            SkillCategory.EXPERIMENT,
            modes=["RESEARCH"],
            timeout=600,
            risks=["NO_LEDGER_WRITE", "NOT_PROOF_OF_PROFITABILITY"],
        ),
        _contract(
            "paper_account_review",
            "Paper账户复核",
            "只操作隔离的模拟账户。",
            SkillCategory.REVIEW,
            modes=["PAPER"],
            read_only=False,
            risks=["PAPER_ONLY"],
        ),
        _contract(
            "manual_trade_tracking",
            "人工成交跟踪",
            "两阶段确认后才可写人工账本。",
            SkillCategory.WORKSPACE,
            modes=["MANUAL_LEDGER"],
            read_only=False,
            confirmation=True,
            ledger_write=True,
            risks=["MANUAL_LEDGER_ONLY", "NO_BROKER_EXECUTION"],
        ),
        _contract(
            "daily_review",
            "每日复盘",
            "生成研究复盘，不生成订单。",
            SkillCategory.REVIEW,
            modes=["RESEARCH"],
            research_write=True,
            risks=["NO_ORDER_CREATED"],
        ),
        _contract(
            "premarket_brief",
            "盘前简报",
            "只生成盘前研究简报。",
            SkillCategory.REVIEW,
            modes=["RESEARCH"],
            research_write=True,
            risks=["BRIEF_ONLY"],
        ),
        _contract(
            "experiment_report",
            "实验报告",
            "读取既有实验与回测评价。",
            SkillCategory.EXPERIMENT,
            modes=["RESEARCH"],
            risks=["NOT_PROOF_OF_PROFITABILITY"],
        ),
        _contract(
            "system_doctor",
            "系统诊断",
            "只读诊断并给出建议，不自动修复。",
            SkillCategory.SYSTEM,
            modes=["SYSTEM"],
            risks=["NO_MUTATIONS_PERFORMED"],
        ),
    ]
)


def skill_catalog_payload(
    registry: SkillRegistry = SKILL_REGISTRY,
) -> dict[str, Any]:
    contracts = registry.list()
    return {
        "skills": [item.model_dump(mode="json") for item in contracts],
        "skill_count": len(contracts),
        "shared_contract": True,
        "order_capability": False,
    }


__all__ = [
    "SKILL_REGISTRY",
    "SkillContract",
    "SkillInvocation",
    "SkillRegistry",
    "SkillResultCard",
    "skill_catalog_payload",
]
