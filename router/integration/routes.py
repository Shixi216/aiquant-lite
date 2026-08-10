from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, TypeVar

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from router.integration.health import SystemHealthService
from router.integration.schemas import (
    DecisionWorkflowRequest,
    DecisionWorkflowResult,
    DoctorReport,
    ErrorCode,
    HealthStatus,
    OpenAPISummary,
    ParseWorkflowRequest,
    ParseWorkflowResult,
    ResearchWorkflowRequest,
    ResearchWorkflowResult,
    ResponseStatus,
    ScanWorkflowRequest,
    ScanWorkflowResult,
    SystemHealth,
    ToolCatalog,
    UnifiedResponse,
    WeComSimulationRequest,
    WeComSimulationResult,
)
from router.integration.tools import TOOL_CATALOG
from router.integration.wecom import LocalWeComAdapter
from router.integration.workflow import (
    InteractionWorkflowService,
    new_request_id,
    sanitize_error,
)
from trading.experiments.schemas import ExperimentReportResponse
from trading.experiments.service import ExperimentEvaluationService


router = APIRouter(prefix="/v1/integration", tags=["system-integration-v1"])
workflow_service = InteractionWorkflowService()
wecom_adapter = LocalWeComAdapter()
DataT = TypeVar("DataT")


def _response(
    data: DataT,
    *,
    request_id: str,
    data_cutoff: datetime | None = None,
    status: ResponseStatus = ResponseStatus.SUCCESS,
    warnings: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> UnifiedResponse[DataT]:
    return UnifiedResponse[DataT](
        request_id=request_id,
        status=status,
        data=data,
        warnings=warnings or [],
        risk_flags=risk_flags or [],
        data_cutoff=data_cutoff,
        generated_at=datetime.now().astimezone(),
    )


def _error_response(
    exc: Exception,
    *,
    request_id: str,
    data_cutoff: datetime | None = None,
) -> JSONResponse:
    if isinstance(exc, ValueError):
        status_code = 422
        error_code = ErrorCode.INVALID_REQUEST
    elif isinstance(exc, KeyError):
        status_code = 404
        error_code = ErrorCode.MISSING_DATA
    elif isinstance(exc, PermissionError):
        status_code = 403
        error_code = ErrorCode.PERMISSION_DENIED
    elif isinstance(exc, TimeoutError):
        status_code = 504
        error_code = ErrorCode.TIMEOUT
    elif isinstance(exc, ConnectionError):
        status_code = 503
        error_code = ErrorCode.NETWORK_ERROR
    elif isinstance(exc, RuntimeError):
        status_code = 503
        error_code = ErrorCode.PROVIDER_ERROR
    else:
        status_code = 503
        error_code = ErrorCode.INTERNAL_ERROR
    payload = UnifiedResponse[dict[str, Any]](
        request_id=request_id,
        status=ResponseStatus.FAILED,
        error_code=error_code,
        sanitized_error=sanitize_error(exc),
        data_cutoff=data_cutoff,
        generated_at=datetime.now().astimezone(),
        risk_flags=["REQUEST_FAILED"],
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


@router.get(
    "/tools",
    response_model=UnifiedResponse[ToolCatalog],
    summary="List the bounded Hermes Finance MCP tool contract",
    description="Returns schemas and risk metadata; never exposes credentials or raw SQL.",
)
def tool_catalog() -> UnifiedResponse[ToolCatalog]:
    return _response(TOOL_CATALOG, request_id=new_request_id())


@router.post(
    "/workflow/parse",
    response_model=UnifiedResponse[ParseWorkflowResult],
    summary="Parse a natural-language screening request",
    description="Local deterministic parsing only; no scan, decision, model, or network call.",
)
def workflow_parse(
    request: ParseWorkflowRequest,
) -> UnifiedResponse[ParseWorkflowResult] | JSONResponse:
    request_id = new_request_id()
    try:
        result = workflow_service.parse(request, request_id=request_id)
        return _response(
            result,
            request_id=request_id,
            data_cutoff=request.data_cutoff,
        )
    except Exception as exc:
        return _error_response(
            exc,
            request_id=request_id,
            data_cutoff=request.data_cutoff,
        )


@router.post(
    "/workflow/scan",
    response_model=UnifiedResponse[ScanWorkflowResult],
    summary="Scan the local A-share snapshot for 10-30 research candidates",
    description="Read-only local scan with zero network and model calls; not a recommendation.",
)
def workflow_scan(
    request: ScanWorkflowRequest,
) -> UnifiedResponse[ScanWorkflowResult] | JSONResponse:
    request_id = new_request_id()
    try:
        result = workflow_service.scan(request, request_id=request_id)
        scan = result.scan
        warnings = ["STALE_SNAPSHOT"] if scan.get("stale") else []
        return _response(
            result,
            request_id=request_id,
            data_cutoff=request.data_cutoff,
            status=(
                ResponseStatus.DEGRADED
                if warnings
                else ResponseStatus.SUCCESS
            ),
            warnings=warnings,
            risk_flags=["NOT_A_TRADE_RECOMMENDATION"],
        )
    except Exception as exc:
        return _error_response(
            exc,
            request_id=request_id,
            data_cutoff=request.data_cutoff,
        )


@router.post(
    "/workflow/research",
    response_model=UnifiedResponse[ResearchWorkflowResult],
    summary="Research at most 30 selected candidates with deep analysis capped at ten",
    description="External fetching is disabled; optional snapshot writes require confirmation.",
)
def workflow_research(
    request: ResearchWorkflowRequest,
) -> UnifiedResponse[ResearchWorkflowResult] | JSONResponse:
    request_id = new_request_id()
    try:
        result = workflow_service.research(request, request_id=request_id)
        missing = sum(
            len(item.get("missing_factors") or [])
            for item in result.research.get("results", [])
        )
        warnings = ["FACTOR_DATA_GAP"] if missing else []
        return _response(
            result,
            request_id=request_id,
            data_cutoff=request.data_cutoff,
            status=(
                ResponseStatus.DEGRADED
                if warnings
                else ResponseStatus.SUCCESS
            ),
            warnings=warnings,
            risk_flags=["RESEARCH_ONLY"],
        )
    except Exception as exc:
        return _error_response(
            exc,
            request_id=request_id,
            data_cutoff=request.data_cutoff,
        )


@router.post(
    "/workflow/decision",
    response_model=UnifiedResponse[DecisionWorkflowResult],
    summary="Run one explicitly confirmed formal and shadow decision evaluation",
    description="Formal 60/40 and shadow outputs remain separate; no packet or order is created.",
)
def workflow_decision(
    request: DecisionWorkflowRequest,
) -> UnifiedResponse[DecisionWorkflowResult] | JSONResponse:
    request_id = new_request_id()
    try:
        result = workflow_service.decision(request, request_id=request_id)
        warnings = (
            ["INSUFFICIENT_FUNDAMENTAL_COVERAGE"]
            if "FUNDAMENTAL" in result.missing_factors
            else []
        )
        risk_flags = ["NO_ORDER_CREATED"]
        if result.formal_result.get("hard_veto"):
            risk_flags.append("HARD_RISK_VETO")
        return _response(
            result,
            request_id=request_id,
            data_cutoff=request.data_cutoff,
            status=(
                ResponseStatus.DEGRADED
                if warnings
                else ResponseStatus.SUCCESS
            ),
            warnings=warnings,
            risk_flags=risk_flags,
        )
    except Exception as exc:
        return _error_response(
            exc,
            request_id=request_id,
            data_cutoff=request.data_cutoff,
        )


@router.post(
    "/wecom/simulate",
    response_model=UnifiedResponse[WeComSimulationResult],
    summary="Render a WeCom message locally without sending it",
    description="Idempotent local adapter; credentials are optional and network sending is disabled.",
)
def wecom_simulate(
    request: WeComSimulationRequest,
) -> UnifiedResponse[WeComSimulationResult] | JSONResponse:
    request_id = new_request_id()
    try:
        result = wecom_adapter.simulate(request)
        not_configured = result.configuration_status == "NOT_CONFIGURED"
        return _response(
            result,
            request_id=request_id,
            status=(
                ResponseStatus.NOT_READY
                if not_configured
                else ResponseStatus.SUCCESS
            ),
            warnings=(
                ["WECOM_NOT_CONFIGURED"] if not_configured else []
            ),
            risk_flags=["LOCAL_SIMULATION_ONLY", "NO_NETWORK_SEND"],
        )
    except Exception as exc:
        return _error_response(exc, request_id=request_id)


@router.get(
    "/system/health",
    response_model=UnifiedResponse[SystemHealth],
    summary="Read the non-mutating system health summary",
    description="Checks local services, database, migrations, freshness, disk, and safety boundaries.",
)
def system_health() -> UnifiedResponse[SystemHealth]:
    request_id = new_request_id()
    health = SystemHealthService().collect()
    status = {
        HealthStatus.HEALTHY: ResponseStatus.SUCCESS,
        HealthStatus.DEGRADED: ResponseStatus.DEGRADED,
        HealthStatus.NOT_READY: ResponseStatus.NOT_READY,
        HealthStatus.FAILED: ResponseStatus.FAILED,
    }[health.overall_status]
    return _response(
        health,
        request_id=request_id,
        status=status,
        warnings=[
            component.detail
            for component in health.components
            if component.status != HealthStatus.HEALTHY
        ],
        risk_flags=["LIVE_TRADING_NOT_SUPPORTED"],
    )


@router.get(
    "/system/doctor",
    response_model=UnifiedResponse[DoctorReport],
    summary="Return read-only system remediation advice",
    description="Doctor reports recommendations only and never changes files or the database.",
)
def system_doctor() -> UnifiedResponse[DoctorReport]:
    report = SystemHealthService().doctor()
    return _response(
        report,
        request_id=new_request_id(),
        status=ResponseStatus.DEGRADED,
        risk_flags=["NO_MUTATIONS_PERFORMED"],
    )


def _openapi_summary(name: str, spec: dict[str, Any]) -> OpenAPISummary:
    operations: list[tuple[str, str, dict[str, Any]]] = []
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                operations.append((method.upper(), path, operation))
    operation_ids = [
        operation.get("operationId")
        for _, _, operation in operations
        if operation.get("operationId")
    ]
    duplicate_ids = sorted(
        name
        for name, count in Counter(operation_ids).items()
        if count > 1
    )
    schemas = spec.get("components", {}).get("schemas", {})
    return OpenAPISummary(
        service=name,
        path_count=len(spec.get("paths", {})),
        operation_count=len(operations),
        schema_count=len(schemas),
        duplicate_operation_ids=duplicate_ids,
        duplicate_schema_names=[],
        undocumented_operations=[
            f"{method} {path}"
            for method, path, operation in operations
            if not operation.get("summary") or not operation.get("description")
        ],
    )


@router.get(
    "/openapi-summary",
    response_model=UnifiedResponse[list[OpenAPISummary]],
    summary="Summarize Router and Data Hub OpenAPI contracts",
    description="Reports path, operation, schema, duplicate, and documentation counts.",
)
def openapi_summary(
    request: Request,
) -> UnifiedResponse[list[OpenAPISummary]]:
    from data_hub.api.app import app as data_hub_app

    summaries = [
        _openapi_summary("router", request.app.openapi()),
        _openapi_summary("data_hub", data_hub_app.openapi()),
    ]
    return _response(summaries, request_id=new_request_id())


@router.get(
    "/experiments/{run_id}",
    response_model=UnifiedResponse[ExperimentReportResponse],
    summary="Read an existing research experiment report",
    description="Read-only access; results never prove stable profitability.",
)
def experiment_report(
    run_id: str,
) -> UnifiedResponse[ExperimentReportResponse] | JSONResponse:
    request_id = new_request_id()
    try:
        result = ExperimentEvaluationService().report(run_id)
        return _response(
            result,
            request_id=request_id,
            data_cutoff=result.run.data_cutoff,
            status=ResponseStatus.DEGRADED,
            warnings=["FORMAL_60_40_INSUFFICIENT_COVERAGE"],
            risk_flags=["NOT_PROOF_OF_PROFITABILITY"],
        )
    except Exception as exc:
        return _error_response(exc, request_id=request_id)


__all__ = ["router", "wecom_adapter", "workflow_service"]
