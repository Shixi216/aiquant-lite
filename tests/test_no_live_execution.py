from __future__ import annotations

from scripts.check_no_live_execution import (
    ALLOWED_FINANCE_MCP_TOOLS,
    broker_import_issues,
    config_issues,
    decision_to_real_order_issues,
    dependency_issues,
    finance_mcp_issues,
    finance_mcp_tool_names,
    indirect_execution_issues,
    load_openapi_schema,
    manual_trade_boundary_issues,
    openapi_issues,
    stage5_review_boundary_issues,
    stage6_package_structure_issues,
)


def test_no_broker_dependencies() -> None:
    assert dependency_issues() == []


def test_no_broker_sdk_imports() -> None:
    assert broker_import_issues() == []


def test_no_broker_credentials_in_settings() -> None:
    assert config_issues() == []


def test_no_live_or_broker_openapi_routes() -> None:
    schema = load_openapi_schema()
    assert openapi_issues(schema) == []


def test_finance_mcp_is_read_only() -> None:
    assert finance_mcp_issues() == []
    assert finance_mcp_tool_names() == ALLOWED_FINANCE_MCP_TOOLS


def test_no_gui_or_browser_trade_execution() -> None:
    assert indirect_execution_issues() == []


def test_no_decision_to_real_order_path() -> None:
    assert decision_to_real_order_issues() == []


def test_manual_trade_boundaries_are_isolated() -> None:
    assert manual_trade_boundary_issues() == []


def test_stage5_review_boundaries_are_read_only() -> None:
    assert stage5_review_boundary_issues() == []


def test_stage6_package_structure_preserves_contracts() -> None:
    assert stage6_package_structure_issues() == []
