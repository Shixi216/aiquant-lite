"""Canonical-plane read-only wrappers (single httpx touchpoint: gateway)."""
from webui.canonical.canonical import (
    cross_section_latest,
    cross_section_symbol,
    data_coverage,
    decision_packet_latest,
    latest_quotes,
    market_breadth,
    equity_context_overview,
    equity_context_stock,
    dragon_tiger_board,
    providers_health,
    router_health,
    scanner_run_candidates,
    scanner_runs,
)

__all__ = [
    "cross_section_latest",
    "cross_section_symbol",
    "data_coverage",
    "decision_packet_latest",
    "latest_quotes",
    "market_breadth",
    "equity_context_overview",
    "equity_context_stock",
    "dragon_tiger_board",
    "providers_health",
    "router_health",
    "scanner_run_candidates",
    "scanner_runs",
]
