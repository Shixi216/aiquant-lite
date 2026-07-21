from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from config.settings import settings
from data_hub.services import (
    AnnouncementService,
    DailyBarsService,
    FinanceNewsService,
    FinancialStatementService,
    MarketFactService,
    RealtimeQuoteService,
)


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
def get_realtime_quote(symbol: str, persist: bool = True) -> dict[str, Any]:
    """Return an A-share realtime quote or a verified latest-close fallback."""

    return _json(RealtimeQuoteService().get_realtime_quote(symbol, persist=persist))


@mcp.tool()
def get_daily_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    persist: bool = True,
) -> dict[str, Any]:
    """Return cross-source daily bars for an A-share symbol and date range."""

    return _json(
        DailyBarsService().get_daily_bars(
            symbol,
            start_date,
            end_date,
            persist=persist,
        )
    )


@mcp.tool()
def get_financial_statement(
    symbol: str,
    start_date: str,
    end_date: str,
    persist: bool = True,
) -> dict[str, Any]:
    """Return the three major financial statements for one reporting period."""

    return _json(
        FinancialStatementService().get_financial_statement(
            symbol,
            start_date,
            end_date,
            persist=persist,
        )
    )


@mcp.tool()
def list_announcements(
    symbol: str,
    start_date: str,
    end_date: str,
    keyword: str = "",
    category: str = "",
    persist: bool = True,
) -> dict[str, Any]:
    """List source-attributed listed-company announcements from CNInfo."""

    return _json(
        AnnouncementService().get_announcements(
            symbol,
            start_date,
            end_date,
            keyword=keyword,
            category=category,
            persist=persist,
        )
    )


@mcp.tool()
def search_finance_news(
    symbol: str,
    query: str | None = None,
    limit: int = 100,
    persist: bool = True,
) -> dict[str, Any]:
    """Search stock-related media reports; results remain explicitly unverified."""

    return _json(
        FinanceNewsService().get_finance_news(
            symbol,
            query=query,
            limit=limit,
            persist=persist,
        )
    )


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
) -> dict[str, Any]:
    """Verify one fact against persisted records and return its evidence trail."""

    return _json(
        MarketFactService().verify_market_fact(
            symbol=symbol,
            data_type=data_type,
            field=field,
            expected_value=expected_value,
            event_date=event_date,
            tolerance=tolerance,
        )
    )


def main() -> None:
    transport = settings.opc_mcp_transport.strip().lower()
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError("OPC_MCP_TRANSPORT must be stdio, sse, or streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
