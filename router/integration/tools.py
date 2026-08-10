from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from data_hub.schemas.full_market import MarketSnapshotResponse, StockUniverseRecord
from data_hub.schemas.service import (
    AnnouncementResponse,
    DailyBarsResponse,
    FinanceNewsResponse,
    FinancialStatementResponse,
    MarketFactVerificationResponse,
    RealtimeQuoteResponse,
)
from router.integration.schemas import (
    DecisionWorkflowRequest,
    DecisionWorkflowResult,
    ResearchWorkflowRequest,
    ResearchWorkflowResult,
    ToolCatalog,
    ToolMetadata,
)
from router.integration.skills import SKILL_REGISTRY
from trading.experiments.schemas import ExperimentReportResponse
from trading.scanner.schemas import ScannerParseResponse, ScannerScanResponse


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StockBasicToolInput(ToolInput):
    symbol: str = Field(pattern=r"^\d{6}\.(?:SH|SZ|BJ)$")


class MarketSnapshotToolInput(ToolInput):
    maximum_age_seconds: int = Field(default=300, ge=0, le=86_400)
    limit: int = Field(default=100, ge=1, le=5000)


class RealtimeQuoteToolInput(StockBasicToolInput):
    persist: Literal[False] = False


class DailyBarsToolInput(StockBasicToolInput):
    start_date: str = Field(pattern=r"^\d{8}$")
    end_date: str = Field(pattern=r"^\d{8}$")
    persist: Literal[False] = False


class FinancialStatementToolInput(DailyBarsToolInput):
    pass


class AnnouncementToolInput(DailyBarsToolInput):
    keyword: str = Field(default="", max_length=100)
    category: str = Field(default="", max_length=100)


class FinanceNewsToolInput(StockBasicToolInput):
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=100, ge=1, le=100)
    persist: Literal[False] = False


class VerifyMarketFactToolInput(StockBasicToolInput):
    data_type: Literal[
        "stock_basic",
        "realtime_quote",
        "daily_bar",
        "financial_statement",
        "announcement",
        "finance_news",
    ]
    field: str = Field(min_length=1, max_length=100)
    expected_value: str | int | float | bool
    event_date: str | None = None
    tolerance: float = Field(default=0.005, ge=0, le=0.25)


class ScannerParseToolInput(ToolInput):
    query: str = Field(min_length=1, max_length=2000)
    top_n: int = Field(default=20, ge=10, le=30)


class ScannerScanToolInput(ScannerParseToolInput):
    pass


class ExperimentReportToolInput(ToolInput):
    run_id: str = Field(min_length=1, max_length=128)


def _schema(model: Any) -> dict[str, Any]:
    return TypeAdapter(model).json_schema()


def _metadata(
    *,
    name: str,
    description: str,
    input_model: Any,
    output_model: Any,
    timeout: int,
    read_only: bool,
    confirmation: bool,
    cutoff: str,
    risk: str,
) -> ToolMetadata:
    return ToolMetadata(
        name=name,
        description=description,
        input_schema=_schema(input_model),
        output_schema=_schema(output_model),
        timeout_seconds=timeout,
        read_only=read_only,
        requires_confirmation=confirmation,
        data_cutoff_rule=cutoff,
        risk_statement=risk,
    )


TOOL_CATALOG = ToolCatalog(
    tools=[
        _metadata(
            name="get_stock_basic",
            description="Read one stock from the current versioned A-share universe.",
            input_model=StockBasicToolInput,
            output_model=StockUniverseRecord,
            timeout=10,
            read_only=True,
            confirmation=False,
            cutoff="latest version at or before the request context",
            risk="Basic information may be stale; it is not a trade recommendation.",
        ),
        _metadata(
            name="get_market_snapshot",
            description="Read the latest persisted full-market snapshot.",
            input_model=MarketSnapshotToolInput,
            output_model=MarketSnapshotResponse,
            timeout=15,
            read_only=True,
            confirmation=False,
            cutoff="latest persisted snapshot; stale status must be preserved",
            risk="Stale or partial snapshots must not be interpreted as live quotes.",
        ),
        _metadata(
            name="get_realtime_quote",
            description="Read a quote without persisting provider output.",
            input_model=RealtimeQuoteToolInput,
            output_model=RealtimeQuoteResponse,
            timeout=60,
            read_only=True,
            confirmation=False,
            cutoff="provider response time, with explicit fallback semantics",
            risk="A fallback close is not a realtime quote.",
        ),
        _metadata(
            name="get_daily_bars",
            description="Read source-attributed historical daily bars.",
            input_model=DailyBarsToolInput,
            output_model=DailyBarsResponse,
            timeout=120,
            read_only=True,
            confirmation=False,
            cutoff="end_date must not exceed the caller's explicit data cutoff",
            risk="Prices use RAW adjustment unless the response states otherwise.",
        ),
        _metadata(
            name="get_financial_statement",
            description="Read the three major financial statements for one period.",
            input_model=FinancialStatementToolInput,
            output_model=FinancialStatementResponse,
            timeout=120,
            read_only=True,
            confirmation=False,
            cutoff="only statements available by the caller's cutoff may be used",
            risk="Point-in-time fundamental coverage is incomplete.",
        ),
        _metadata(
            name="list_announcements",
            description="Read source-attributed listed-company announcements.",
            input_model=AnnouncementToolInput,
            output_model=AnnouncementResponse,
            timeout=120,
            read_only=True,
            confirmation=False,
            cutoff="announcement time must be at or before the caller's cutoff",
            risk="Missing announcements must be disclosed, never inferred.",
        ),
        _metadata(
            name="search_finance_news",
            description="Read stock-related media reports.",
            input_model=FinanceNewsToolInput,
            output_model=FinanceNewsResponse,
            timeout=120,
            read_only=True,
            confirmation=False,
            cutoff="publication time must be at or before the caller's cutoff",
            risk="Media reports remain unverified until corroborated.",
        ),
        _metadata(
            name="verify_market_fact",
            description="Verify one fact against persisted source evidence.",
            input_model=VerifyMarketFactToolInput,
            output_model=MarketFactVerificationResponse,
            timeout=30,
            read_only=True,
            confirmation=False,
            cutoff="event_date selects evidence no later than that date",
            risk="A missing match is not proof that the claim is false.",
        ),
        _metadata(
            name="parse_market_scanner_query",
            description="Parse natural language into a deterministic query plan.",
            input_model=ScannerParseToolInput,
            output_model=ScannerParseResponse,
            timeout=5,
            read_only=True,
            confirmation=False,
            cutoff="uses the request time unless an explicit API cutoff is supplied",
            risk="Parsing does not execute a scan or produce a recommendation.",
        ),
        _metadata(
            name="scan_a_share_market",
            description="Return 10-30 local research candidates.",
            input_model=ScannerScanToolInput,
            output_model=ScannerScanResponse | ScannerParseResponse,
            timeout=15,
            read_only=True,
            confirmation=False,
            cutoff="latest eligible local snapshot at the request cutoff",
            risk="Ranking is research priority only and is not a trade recommendation.",
        ),
        _metadata(
            name="research_candidates",
            description="Research at most 30 selected candidates; deep analysis is capped at ten.",
            input_model=ResearchWorkflowRequest,
            output_model=ResearchWorkflowResult,
            timeout=120,
            read_only=False,
            confirmation=True,
            cutoff="explicit timezone-aware data_cutoff is mandatory",
            risk="Optional snapshot persistence is bounded and requires confirmation.",
        ),
        _metadata(
            name="create_explicit_decision",
            description="Evaluate one explicitly selected symbol using formal and shadow results.",
            input_model=DecisionWorkflowRequest,
            output_model=DecisionWorkflowResult,
            timeout=60,
            read_only=True,
            confirmation=True,
            cutoff="explicit timezone-aware data_cutoff is mandatory",
            risk="No order is created; hard VETO remains final.",
        ),
        _metadata(
            name="get_experiment_report",
            description="Read an existing experiment evaluation report.",
            input_model=ExperimentReportToolInput,
            output_model=ExperimentReportResponse,
            timeout=30,
            read_only=True,
            confirmation=False,
            cutoff="uses the immutable cutoff stored on the experiment run",
            risk="Historical results do not prove stable profitability.",
        ),
    ],
    tool_count=13,
)

TOOL_NAMES = tuple(tool.name for tool in TOOL_CATALOG.tools)
HERMES_SKILL_REGISTRY = SKILL_REGISTRY


def validate_tool_catalog() -> list[str]:
    issues: list[str] = []
    names = [tool.name for tool in TOOL_CATALOG.tools]
    if len(names) != len(set(names)):
        issues.append("duplicate tool names")
    if TOOL_CATALOG.tool_count != len(names):
        issues.append("tool_count does not match tools")
    if any("order" in name.casefold() for name in names):
        issues.append("order tool exposed")
    for tool in TOOL_CATALOG.tools:
        if not tool.input_schema or not tool.output_schema:
            issues.append(f"{tool.name} has an empty schema")
        if not tool.risk_statement:
            issues.append(f"{tool.name} has no risk statement")
    return issues


__all__ = [
    "HERMES_SKILL_REGISTRY",
    "TOOL_CATALOG",
    "TOOL_NAMES",
    "validate_tool_catalog",
]
