from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from config.settings import settings
from mcp_servers.finance_data.server import (
    mcp,
    parse_market_scanner_query,
)
from router.api.app import app
from router.integration.health import (
    SystemHealthService,
    migration_checksum_state,
)
from router.integration.schemas import (
    DecisionWorkflowRequest,
    DecisionWorkflowResult,
    HealthStatus,
    ResearchWorkflowRequest,
    WeComMessageType,
    WeComSimulationRequest,
)
from router.integration.tools import (
    TOOL_CATALOG,
    TOOL_NAMES,
    validate_tool_catalog,
)
from router.integration.wecom import LocalWeComAdapter
from router.integration.workflow import new_request_id, sanitize_error
from scripts.check_no_live_execution import (
    CURRENT_ROUTE_METHODS,
    finance_mcp_issues,
    openapi_issues,
    route_methods,
)
from trading.research.orchestration.decision import formal_result
from trading.research.orchestration.schemas import DecisionShadowRequest
from trading.scanner.feature_loader import ScannerFeatureLoader
from trading.scanner.schemas import ScannerScanRequest
from trading.scanner.service import MarketScannerService
from trading.schemas import Action


client = TestClient(app)
NOW = datetime.now().astimezone()


def _business_counts() -> dict[str, int]:
    names = (
        "experiment_definitions",
        "experiment_runs",
        "experiment_observations",
        "forward_return_labels",
        "backtest_runs",
        "backtest_positions",
        "decision_packets",
        "paper_accounts",
        "trading_orders",
    )
    with duckdb.connect(str(settings.opc_database_path), read_only=True) as connection:
        return {
            name: int(
                connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            )
            for name in names
        }


def test_01_unified_response_has_required_fields() -> None:
    response = client.get("/v1/integration/tools")
    assert response.status_code == 200
    assert set(
        (
            "request_id",
            "status",
            "data",
            "error_code",
            "sanitized_error",
            "warnings",
            "risk_flags",
            "data_cutoff",
            "generated_at",
            "api_version",
        )
    ).issubset(response.json())
    assert response.json()["research_only"] is True


def test_02_tool_catalog_is_valid_and_complete() -> None:
    assert validate_tool_catalog() == []
    assert TOOL_CATALOG.tool_count == 13
    assert {tool.name for tool in TOOL_CATALOG.tools} == set(TOOL_NAMES)


def test_03_mcp_names_align_with_catalog() -> None:
    actual = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert actual == set(TOOL_NAMES)
    assert finance_mcp_issues() == []


def test_04_every_tool_has_schema_timeout_and_risk_metadata() -> None:
    for tool in TOOL_CATALOG.tools:
        assert tool.input_schema["type"] == "object"
        assert (
            tool.output_schema.get("type") == "object"
            or bool(tool.output_schema.get("anyOf"))
        )
        assert tool.timeout_seconds > 0
        assert tool.data_cutoff_rule
        assert tool.risk_statement


def test_04b_mcp_input_and_output_schemas_align_with_catalog() -> None:
    metadata = {tool.name: tool for tool in TOOL_CATALOG.tools}
    for actual in mcp._tool_manager.list_tools():
        contract = metadata[actual.name]
        assert set(actual.parameters.get("properties", {})) == set(
            contract.input_schema.get("properties", {})
        )
        assert actual.output_schema
        if contract.output_schema.get("properties"):
            assert set(actual.output_schema.get("properties", {})) == set(
                contract.output_schema["properties"]
            )


def test_05_no_order_or_raw_database_tool_exists() -> None:
    names = {tool.name.casefold() for tool in TOOL_CATALOG.tools}
    assert not any("order" in name or "sql" in name or "table" in name for name in names)
    assert TOOL_CATALOG.order_tools_supported is False
    assert TOOL_CATALOG.raw_database_access_supported is False


def test_06_parse_mcp_invocation_is_local_and_non_recommendation() -> None:
    result = parse_market_scanner_query("筛选A股前20只", top_n=20)
    assert result["parser_model_call_count"] == 0
    assert result["is_trade_recommendation"] is False


def test_07_parse_endpoint_never_calls_network_or_decision() -> None:
    response = client.post(
        "/v1/integration/workflow/parse",
        json={
            "query": "筛选A股前20只",
            "top_n": 20,
            "data_cutoff": NOW.isoformat(),
        },
    )
    assert response.status_code == 200
    workflow = response.json()["data"]["workflow"]
    assert workflow["network_request_count"] == 0
    assert workflow["decision_called"] is False
    assert workflow["order_created"] is False
    scan = client.post(
        "/v1/integration/workflow/scan",
        json={
            "query": "筛选A股前20只",
            "top_n": 20,
            "data_cutoff": NOW.isoformat(),
            "parent_request_id": response.json()["request_id"],
        },
    )
    assert scan.status_code == 200
    scan_data = scan.json()["data"]
    assert scan_data["workflow"]["parent_request_id"] == response.json()["request_id"]
    assert scan_data["scan"]["performance"]["network_request_count"] == 0
    assert scan_data["scan"]["performance"]["model_call_count"] == 0
    assert scan_data["scan"]["decision_called"] is False
    assert scan_data["scan"]["returned_count"] == 20


def test_08_research_accepts_at_most_thirty_symbols() -> None:
    with pytest.raises(ValidationError):
        ResearchWorkflowRequest(
            symbols=[f"{index:06d}.SZ" for index in range(31)],
            data_cutoff=NOW,
            parent_request_id=new_request_id(),
        )


def test_09_research_snapshot_write_requires_confirmation() -> None:
    with pytest.raises(ValidationError, match="explicit confirmation"):
        ResearchWorkflowRequest(
            symbols=["600000.SH"],
            data_cutoff=NOW,
            parent_request_id=new_request_id(),
            capture_snapshot=True,
        )


def test_10_decision_requires_exact_symbol_confirmation() -> None:
    with pytest.raises(ValidationError, match="CONFIRM_DECISION"):
        DecisionWorkflowRequest(
            symbol="600000.SH",
            data_cutoff=NOW,
            parent_request_id=new_request_id(),
            technical_score=0.5,
            technical_confidence=0.8,
            fundamental_score=0.2,
            fundamental_confidence=0.5,
            confirmation="yes",
        )


def test_11_validation_errors_use_sanitized_envelope() -> None:
    response = client.post(
        "/v1/integration/workflow/decision",
        json={
            "symbol": "600000.SH",
            "data_cutoff": NOW.isoformat(),
            "parent_request_id": new_request_id(),
            "technical_score": 0.5,
            "technical_confidence": 0.8,
            "fundamental_score": 0.2,
            "fundamental_confidence": 0.5,
            "confirmation": "wrong",
        },
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == "INVALID_REQUEST"
    assert "wrong" not in payload["sanitized_error"]


def test_12_formal_decision_remains_60_40() -> None:
    result = formal_result(
        DecisionShadowRequest(
            symbol="600000.SH",
            data_cutoff=NOW,
            technical_score=1,
            technical_confidence=0.8,
            fundamental_score=0,
            fundamental_confidence=0.4,
            persist_shadow=False,
        )
    )
    assert result.formal_weights == {"TECHNICAL": 0.6, "FUNDAMENTAL": 0.4}
    assert result.score == pytest.approx(0.6)
    assert result.shadow_used_for_action is False


def test_13_hard_veto_is_final() -> None:
    result = formal_result(
        DecisionShadowRequest(
            symbol="600000.SH",
            data_cutoff=NOW,
            technical_score=1,
            technical_confidence=1,
            fundamental_score=1,
            fundamental_confidence=1,
            hard_veto=True,
            persist_shadow=False,
        )
    )
    assert result.proposal_action == Action.BUY
    assert result.final_action == Action.VETO


def test_14_shadow_weight_cannot_be_promoted() -> None:
    with pytest.raises(ValidationError):
        DecisionWorkflowResult(
            workflow={
                "step": "DECISION",
                "parent_request_id": new_request_id(),
                "decision_called": True,
            },
            formal_result={},
            shadow_composite={},
            factor_coverage={},
            available_factors=[],
            missing_factors=[],
            evidence_ids=[],
            formal_weights={"TECHNICAL": 0.6, "FUNDAMENTAL": 0.4},
            shadow_formal_strategy_weight=0.1,
        )


def test_14b_missing_fundamental_suppresses_formal_action() -> None:
    symbol = "603980.SH"
    response = client.post(
        "/v1/integration/workflow/decision",
        json={
            "symbol": symbol,
            "data_cutoff": NOW.isoformat(),
            "parent_request_id": new_request_id(),
            "technical_score": 1,
            "technical_confidence": 1,
            "fundamental_score": 0,
            "fundamental_confidence": 0,
            "hard_veto": False,
            "confirmation": f"CONFIRM_DECISION:{symbol}",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "FUNDAMENTAL" in data["missing_factors"]
    assert data["formal_result"]["status"] == "INSUFFICIENT_COVERAGE"
    assert data["formal_result"]["final_action"] is None


def test_15_wecom_paginates_candidate_lists() -> None:
    adapter = LocalWeComAdapter()
    result = adapter.simulate(
        WeComSimulationRequest(
            message_id="page-case",
            message_type=WeComMessageType.CANDIDATE_LIST,
            text="候选",
            items=[
                {"rank": index, "symbol": f"{index:06d}.SZ"}
                for index in range(1, 24)
            ],
            page=2,
            page_size=10,
        )
    )
    assert result.page == 2
    assert result.total_pages == 3
    assert result.has_next is True


def test_16_wecom_message_id_is_idempotent() -> None:
    adapter = LocalWeComAdapter()
    request = WeComSimulationRequest(
        message_id="same-message",
        message_type=WeComMessageType.STATUS,
        text="status",
    )
    first = adapter.simulate(request)
    second = adapter.simulate(request)
    assert first.duplicate is False
    assert second.duplicate is True
    assert first.rendered_text == second.rendered_text


def test_17_wecom_not_configured_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "WECOM_HOME_CHANNEL",
        "WECOM_SECRET",
        "WECOM_WEBHOOK",
        "WECOM_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    result = LocalWeComAdapter().simulate(
        WeComSimulationRequest(
            message_id="not-configured",
            message_type=WeComMessageType.TEXT_QUERY,
            text="hello",
        )
    )
    assert result.configuration_status == "NOT_CONFIGURED"
    assert result.sent is False


def test_18_wecom_decision_requires_second_confirmation() -> None:
    result = LocalWeComAdapter().simulate(
        WeComSimulationRequest(
            message_id="decision-summary",
            message_type=WeComMessageType.DECISION_SUMMARY,
            text="decision",
        )
    )
    assert result.requires_second_confirmation is True
    assert result.trade_action_available is False


def test_19_wecom_output_sanitizes_secrets_paths_and_reasoning() -> None:
    result = LocalWeComAdapter().simulate(
        WeComSimulationRequest(
            message_id="sanitize",
            message_type=WeComMessageType.STATUS,
            text=(
                "TEST_API_KEY=fake-test-only C:\\private\\file "
                "<analysis>hidden chain</analysis>"
            ),
        )
    )
    assert "fake-test-only" not in result.rendered_text
    assert "C:\\private" not in result.rendered_text
    assert "hidden chain" not in result.rendered_text


def test_20_error_sanitizer_removes_bearer_and_stack() -> None:
    cleaned = sanitize_error(
        "Bearer fake-test-only\nTraceback (most recent call last): secret stack"
    )
    assert "fake-test-only" not in cleaned
    assert "secret stack" not in cleaned


def test_21_health_uses_required_enum_and_ports() -> None:
    health = SystemHealthService().collect()
    assert health.overall_status in set(HealthStatus)
    assert health.router_port == 8765
    assert health.data_hub_port == 8766
    assert health.live_trading_status == "NOT_SUPPORTED"


def test_22_wecom_not_configured_does_not_fail_core_health() -> None:
    health = SystemHealthService().collect()
    wecom = next(item for item in health.components if item.name == "wecom")
    assert wecom.required is False
    assert wecom.status in {HealthStatus.HEALTHY, HealthStatus.NOT_READY}
    assert health.database_read_only_ok is True


def test_23_health_and_doctor_do_not_change_business_counts() -> None:
    before = _business_counts()
    service = SystemHealthService()
    service.collect()
    report = service.doctor()
    assert report.mutations_performed is False
    assert _business_counts() == before


def test_24_migration_0100_0111_checksums_match_without_0112() -> None:
    state = migration_checksum_state()
    assert state["ok"] is True
    assert state["applied_count"] == 12
    assert not Path("database/migrations/v0112_system_integration.py").exists()


def test_25_openapi_preserves_existing_contract_and_adds_stage11() -> None:
    schema = app.openapi()
    actual = route_methods(schema)
    assert actual == CURRENT_ROUTE_METHODS
    assert ("GET", "/v1/integration/tools") in actual
    assert openapi_issues(schema) == []


def test_26_all_openapi_operations_are_documented_and_unique() -> None:
    schema = app.openapi()
    operation_ids: list[str] = []
    for path_item in schema["paths"].values():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert operation.get("summary")
            assert operation.get("description")
            operation_ids.append(operation["operationId"])
    assert len(operation_ids) == len(set(operation_ids))


def test_27_openapi_and_reports_contain_no_configured_token() -> None:
    serialized = json.dumps(app.openapi(), ensure_ascii=False).casefold()
    assert "tushare_token" not in serialized
    assert "api_key=" not in serialized


def test_28_start_status_health_doctor_stop_scripts_exist() -> None:
    names = (
        "start_hermes_opc.ps1",
        "status_hermes_opc.ps1",
        "health_hermes_opc.ps1",
        "doctor_hermes_opc.ps1",
        "stop_hermes_opc.ps1",
    )
    for name in names:
        assert (Path("scripts") / name).is_file()


def test_29_health_and_doctor_scripts_do_not_mutate_project() -> None:
    for name in ("health_hermes_opc.ps1", "doctor_hermes_opc.ps1"):
        text = (Path("scripts") / name).read_text(encoding="utf-8").casefold()
        assert "remove-item" not in text
        assert "set-content" not in text
        assert "out-file" not in text


def test_30_stage11_did_not_generate_experiment_records() -> None:
    assert _business_counts() == {
        "experiment_definitions": 2,
        "experiment_runs": 2,
        "experiment_observations": 4120,
        "forward_return_labels": 16480,
        "backtest_runs": 1,
        "backtest_positions": 160,
        "decision_packets": 0,
        "paper_accounts": 0,
        "trading_orders": 0,
    }


def test_31_scanner_cold_and_hot_performance_contract() -> None:
    ScannerFeatureLoader.clear_cache()
    service = MarketScannerService()
    request = ScannerScanRequest(
        query="全A股前20只",
        top_n=20,
        data_cutoff=NOW,
        persist_run=False,
        allow_parser_model=False,
    )
    cold = service.scan(request)
    hot = service.scan(request)
    assert cold.performance.elapsed_ms <= 15_000
    assert hot.performance.elapsed_ms <= 5_000
    assert cold.performance.database_query_count == 1
    assert hot.performance.database_query_count == 0
    assert cold.performance.network_request_count == 0
    assert hot.performance.model_call_count == 0
