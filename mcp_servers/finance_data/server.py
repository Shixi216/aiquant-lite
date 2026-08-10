from __future__ import annotations

import os
from typing import Any, Literal

os.environ["HERMES_DB_ENFORCE_OWNER"] = "1"
os.environ.pop("HERMES_DB_OWNER_PROCESS", None)

from mcp.server.fastmcp import FastMCP

from config.settings import settings
from config.utf8 import configure_utf8_stdio
from mcp_servers.finance_data.router_client import request_router
from trading.scanner.query_parser import LocalChineseQueryParser
from data_hub.schemas.full_market import (
    MarketSnapshotResponse,
    StockUniverseRecord,
)
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
)
from trading.experiments.schemas import ExperimentReportResponse
from trading.scanner.schemas import ScannerParseResponse, ScannerScanResponse
from trading.scanner.schemas import ScannerParseRequest, ScannerScanRequest


mcp = FastMCP(
    "aiquant-lite Finance Data",
    instructions=(
        "Read-only A-share research data tools. Structured market facts come from "
        "source-attributed providers and must not be invented by the calling model."
    ),
    host=settings.opc_mcp_host,
    port=settings.opc_mcp_port,
    stateless_http=True,
    json_response=True,
)


def _json(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


@mcp.tool()
def get_stock_basic(symbol: str) -> StockUniverseRecord:
    """Read one stock through the single-owner Router."""
    return request_router("GET", f"/v1/universe/{symbol.upper()}")


@mcp.tool()
def get_market_snapshot(
    maximum_age_seconds: int = 300,
    limit: int = 100,
) -> MarketSnapshotResponse:
    """Read the latest persisted snapshot through the Router."""
    return request_router(
        "GET",
        "/v1/market-snapshots/latest",
        params={"maximum_age_seconds": maximum_age_seconds, "limit": limit},
    )


@mcp.tool()
def get_realtime_quote(
    symbol: str,
    persist: bool = False,
) -> RealtimeQuoteResponse:
    """Return a realtime quote through the Router database owner."""
    return request_router(
        "GET", f"/v1/stocks/{symbol}/realtime-quote", params={"persist": persist}
    )


@mcp.tool()
def get_daily_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    persist: bool = False,
) -> DailyBarsResponse:
    """Return daily bars through the Router database owner."""
    return request_router(
        "GET",
        f"/v1/stocks/{symbol}/daily-bars",
        params={"start_date": start_date, "end_date": end_date, "persist": persist},
    )


@mcp.tool()
def get_financial_statement(
    symbol: str,
    start_date: str,
    end_date: str,
    persist: bool = False,
) -> FinancialStatementResponse:
    """Return financial statements through the Router database owner."""
    return request_router(
        "GET",
        f"/v1/stocks/{symbol}/financial-statements",
        params={"start_date": start_date, "end_date": end_date, "persist": persist},
    )


@mcp.tool()
def list_announcements(
    symbol: str,
    start_date: str,
    end_date: str,
    keyword: str = "",
    category: str = "",
    persist: bool = False,
) -> AnnouncementResponse:
    """List announcements through the Router database owner."""
    return request_router(
        "GET",
        f"/v1/stocks/{symbol}/announcements",
        params={
            "start_date": start_date,
            "end_date": end_date,
            "keyword": keyword,
            "category": category,
            "persist": persist,
        },
    )


@mcp.tool()
def search_finance_news(
    symbol: str,
    query: str | None = None,
    limit: int = 100,
    persist: bool = False,
) -> FinanceNewsResponse:
    """Search finance news through the Router database owner."""
    params = {"limit": limit, "persist": persist}
    if query is not None:
        params["query"] = query
    return request_router("GET", f"/v1/stocks/{symbol}/finance-news", params=params)


@mcp.tool()
def verify_market_fact(
    symbol: str,
    data_type: Literal[
        "stock_basic",
        "realtime_quote",
        "daily_bar",
        "financial_statement",
        "announcement",
        "finance_news",
    ],
    field: str,
    expected_value: str | int | float | bool,
    event_date: str | None = None,
    tolerance: float = 0.005,
) -> MarketFactVerificationResponse:
    """Verify one fact through the Router database owner."""
    return request_router(
        "POST",
        "/v1/market-facts/verify",
        payload={
            "symbol": symbol,
            "data_type": data_type,
            "field": field,
            "expected_value": expected_value,
            "event_date": event_date,
            "tolerance": tolerance,
        },
    )


@mcp.tool()
def parse_market_scanner_query(
    query: str,
    top_n: int = 20,
) -> ScannerParseResponse:
    """Parse locally without database, network, or model access."""
    return _json(
        LocalChineseQueryParser().parse(
            ScannerParseRequest(query=query, top_n=top_n)
        )
    )


@mcp.tool()
def scan_a_share_market(
    query: str,
    top_n: int = 20,
) -> ScannerScanResponse | ScannerParseResponse:
    """Scan through the Router database owner."""
    request = ScannerScanRequest(query=query, top_n=top_n, persist_run=False)
    return request_router(
        "POST", "/v1/scanner/scan", payload=request.model_dump(mode="json")
    )


@mcp.tool()
def research_candidates(
    symbols: list[str],
    data_cutoff: str,
    parent_request_id: str,
    capture_snapshot: bool = False,
    confirm_snapshot_capture: bool = False,
    ai_deep_analysis_limit: int = 10,
) -> ResearchWorkflowResult:
    """Research through the Router; the MCP process never opens DuckDB."""
    request = ResearchWorkflowRequest(
        symbols=symbols,
        data_cutoff=data_cutoff,
        parent_request_id=parent_request_id,
        capture_snapshot=capture_snapshot,
        confirm_snapshot_capture=confirm_snapshot_capture,
        ai_deep_analysis_limit=ai_deep_analysis_limit,
    )
    return request_router(
        "POST",
        "/v1/integration/workflow/research",
        payload=request.model_dump(mode="json"),
        unwrap=True,
    )


@mcp.tool()
def create_explicit_decision(
    symbol: str,
    data_cutoff: str,
    parent_request_id: str,
    technical_score: float,
    technical_confidence: float,
    fundamental_score: float,
    fundamental_confidence: float,
    confirmation: str,
    hard_veto: bool = False,
) -> DecisionWorkflowResult:
    """Evaluate through the Router; no packet or order is created."""
    request = DecisionWorkflowRequest(
        symbol=symbol,
        data_cutoff=data_cutoff,
        parent_request_id=parent_request_id,
        technical_score=technical_score,
        technical_confidence=technical_confidence,
        fundamental_score=fundamental_score,
        fundamental_confidence=fundamental_confidence,
        confirmation=confirmation,
        hard_veto=hard_veto,
    )
    return request_router(
        "POST",
        "/v1/integration/workflow/decision",
        payload=request.model_dump(mode="json"),
        unwrap=True,
    )


@mcp.tool()
def get_experiment_report(run_id: str) -> ExperimentReportResponse:
    """Read an existing experiment report through the Router."""
    return request_router("GET", f"/v1/experiment-runs/{run_id}/report")


def main() -> None:
    configure_utf8_stdio()
    transport = settings.opc_mcp_transport.strip().lower()
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError("OPC_MCP_TRANSPORT must be stdio, sse, or streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
