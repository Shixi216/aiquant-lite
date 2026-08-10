from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import httpx
import pytest
from fastapi.testclient import TestClient
from config.settings import settings
from data_hub.schemas.unified import FactorType
from desktop.credentials import (
    MemoryCredentialStore,
    child_process_environment,
)
from desktop.paths import AppPaths
from desktop.runtime import activate_configured_credentials
from desktop.service_supervisor import (
    ServiceRecord,
    ServiceSupervisor,
)
from desktop.settings import DesktopSettings
from desktop.workspace.model_research import (
    ResearchModelError,
    ResearchModelResult,
    RouterResearchClient,
)
from desktop.workspace.service import TaskCenterService
from desktop.workspace.state import DesktopStateRepository
from router.api.app import app
from router.config import router_settings
from router.integration.model_chain_logging import (
    MODEL_CHAIN_FIELDS,
    log_model_chain_stage,
)
from router.registry import RoleSpec
from router.schemas import (
    AnnouncementEvidenceBundle,
    AnnouncementEvidencePage,
    ResearchSynthesisOutput,
    RouterInvokeRequest,
    RouterInvokeResponse,
)
from router.services.announcement_pipeline import (
    AnnouncementVerificationPipelineService,
)
from router.services.audit import AuditStore
from router.services.audit_query import AuditQueryStore
from router.services.invocation import RouterInvocationService
from trading.research.orchestration.decision import formal_result
from trading.research.orchestration.models import FORMAL_WEIGHTS
from trading.research.orchestration.schemas import DecisionShadowRequest
from trading.schemas import Action


def _create_audit_tables(path: Path) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
                task_id VARCHAR PRIMARY KEY,
                task_type VARCHAR NOT NULL,
                symbol VARCHAR,
                status VARCHAR NOT NULL,
                request_json JSON,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE model_calls (
                call_id VARCHAR PRIMARY KEY,
                task_id VARCHAR,
                agent_role VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                model VARCHAR NOT NULL,
                input_tokens BIGINT,
                output_tokens BIGINT,
                latency_ms BIGINT,
                estimated_cost DOUBLE,
                success BOOLEAN NOT NULL,
                error_type VARCHAR,
                error_message VARCHAR,
                prompt_version VARCHAR,
                input_hash VARCHAR,
                retry_count BIGINT,
                schema_validation VARCHAR,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE agent_results (
                result_id VARCHAR PRIMARY KEY,
                task_id VARCHAR NOT NULL,
                agent_role VARCHAR NOT NULL,
                provider VARCHAR,
                model VARCHAR,
                confidence DOUBLE,
                success BOOLEAN NOT NULL,
                result_json JSON,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )


@pytest.fixture()
def audit_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    path = tmp_path / "audit.duckdb"
    _create_audit_tables(path)
    monkeypatch.setattr(settings, "opc_database_path", path)
    return path


def _research_output(symbol: str = "300750.SZ") -> ResearchSynthesisOutput:
    return ResearchSynthesisOutput(
        symbol=symbol,
        summary="现有证据仅支持研究摘要。",
        factor_observations=[
            {
                "factor": "TECHNICAL",
                "status": "AVAILABLE",
                "observation": "技术证据可用。",
            }
        ],
        missing_data=["FUNDAMENTAL"],
        risk_flags=["INSUFFICIENT_COVERAGE"],
        confidence=0.5,
        not_trade_recommendation=True,
    )


class _ResearchClient:
    def __init__(self, *, error: str | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    def synthesize(self, *, request_id, symbol, data_cutoff, local_research):
        del request_id, data_cutoff, local_research
        self.calls.append(symbol)
        if self.error:
            raise ResearchModelError(self.error)
        return ResearchModelResult(
            task_id="task_model_research",
            provider="longcat",
            model="test-model",
            call_ids=("call_model_research",),
            output=_research_output(symbol),
        )


def test_01_stock_research_calls_real_model_path(tmp_path: Path) -> None:
    repository = DesktopStateRepository(tmp_path / "desktop.sqlite3")
    model_client = _ResearchClient()
    service = TaskCenterService(
        repository,
        research_model_client=model_client,
    )
    conversation = service.new_conversation()

    task = service.submit_message(
        conversation.conversation_id,
        "分析宁德时代",
    )

    assert model_client.calls == ["300750.SZ"]
    assert task.result_card is not None
    assert task.result_card.fields["model_call_count"] == 1
    assert task.result_card.fields["model_research"][0]["symbol"] == "300750.SZ"


def test_02_model_call_count_uses_physical_call_ids(tmp_path: Path) -> None:
    result = ResearchModelResult(
        task_id="task_count",
        provider="longcat",
        model="test-model",
        call_ids=("call_1", "call_2"),
        output=_research_output(),
    )
    assert result.model_call_count == 2


def test_03_unconfigured_model_returns_not_configured() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "role": "research_synthesizer",
                "provider": "longcat",
                "enabled": False,
            },
        )
    )
    client = RouterResearchClient(transport=transport)
    with pytest.raises(ResearchModelError, match="NOT_CONFIGURED"):
        client.synthesize(
            request_id="req_" + "a" * 24,
            symbol="300750.SZ",
            data_cutoff=datetime.now().astimezone(),
            local_research={},
        )


def test_04_model_failure_is_not_reported_as_success(tmp_path: Path) -> None:
    repository = DesktopStateRepository(tmp_path / "desktop.sqlite3")
    service = TaskCenterService(
        repository,
        research_model_client=_ResearchClient(error="PROVIDER_ERROR"),
    )
    conversation = service.new_conversation()
    task = service.submit_message(conversation.conversation_id, "分析宁德时代")

    assert task.status.value == "FAILED"
    assert task.error_code == "PROVIDER_ERROR"
    assert task.result_card is None


class _FakeProvider:
    provider_name = "longcat"

    async def invoke(self, **kwargs) -> RouterInvokeResponse:
        return RouterInvokeResponse(
            role=str(kwargs["role"]),
            provider="longcat",
            model="test-model",
            content=json.dumps(
                _research_output().model_dump(mode="json"),
                ensure_ascii=False,
            ),
            latency_ms=3,
            usage={"input_tokens": 2, "output_tokens": 3},
        )


@pytest.mark.asyncio
async def test_05_router_research_creates_one_audited_model_call(
    audit_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audit_database
    role = RoleSpec(
        role="research_synthesizer",
        display_name="research",
        provider="longcat",
        preferred_model="test-model",
        task_type="research_synthesis",
        description="test",
        enabled=True,
    )
    monkeypatch.setattr("router.services.invocation.get_role", lambda name: role)
    monkeypatch.setattr(
        "router.services.invocation.get_model_provider",
        lambda name: _FakeProvider(),
    )

    response = await RouterInvocationService().invoke(
        RouterInvokeRequest(
            request_id="req_" + "b" * 24,
            role="research_synthesizer",
            symbol="300750.SZ",
            prompt="local evidence",
        )
    )

    assert response.validated is True
    assert response.task_id
    assert len(response.call_ids) == 1
    detail = AuditQueryStore().get_task(response.task_id)
    assert detail is not None
    assert detail.status == "completed"
    assert len(detail.model_calls) == 1


def _announcement_bundle() -> AnnouncementEvidenceBundle:
    return AnnouncementEvidenceBundle(
        symbol="600172.SH",
        record_id="record_test",
        title="测试公告",
        short_name="测试",
        announcement_id="1234567890",
        announcement_date="2026-07-30",
        source_name="CNInfo",
        source_url=(
            "https://www.cninfo.com.cn/new/disclosure/detail?"
            "announcementId=1234567890&announcementTime=2026-07-30"
        ),
        source_level="official",
        verified=True,
        source_authenticity="verified_official",
        pdf_url="https://static.cninfo.com.cn/test.pdf",
        pdf_sha256="a" * 64,
        text_sha256="b" * 64,
        page_count=1,
        text_length=20,
        requires_ocr=False,
        pages=[
            AnnouncementEvidencePage(
                page_number=1,
                char_count=20,
                text="公司发布了本公告，内容仅供测试。",
            )
        ],
    )


def _announcement_content() -> str:
    return json.dumps(
        {
            "announcement_id": "1234567890",
            "title": "测试公告",
            "overall_verdict": "SUPPORTED",
            "summary": "公告支持该陈述。",
            "claims": [
                {
                    "claim_id": "claim_1",
                    "claim": "公司发布了本公告。",
                    "verdict": "SUPPORTED",
                    "temporal_nature": "CURRENT_STATUS",
                    "normalized_fact": "公司发布公告。",
                    "evidence": [
                        {
                            "page_number": 1,
                            "quote": "公司发布了本公告",
                        }
                    ],
                    "reason": "公告正文直接支持。",
                    "confidence": 0.9,
                }
            ],
            "warnings": [],
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_06_qwen_success_creates_completed_audit_task(
    audit_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audit_database

    async def fake_build_selected(self, **kwargs):
        del self, kwargs
        return _announcement_bundle()

    async def fake_invoke(self, **kwargs):
        del self
        return RouterInvokeResponse(
            role=str(kwargs["role"]),
            provider="qwen",
            model="test-qwen",
            content=_announcement_content(),
            latency_ms=5,
            usage={},
        )

    monkeypatch.setattr(
        "router.services.announcement_pipeline.AnnouncementEvidenceService.build_selected",
        fake_build_selected,
    )
    monkeypatch.setattr(
        "router.providers.qwen.QwenProvider.invoke",
        fake_invoke,
    )

    result = await AnnouncementVerificationPipelineService().run(
        SimpleNamespace(
            symbol="600172.SH",
            record_id="record_test",
            announcement_id=None,
            start_date=None,
            end_date=None,
            claims=["公司发布了本公告。"],
            max_tokens=1000,
            model_dump=lambda **kwargs: {
                "symbol": "600172.SH",
                "record_id": "record_test",
                "claims": ["公司发布了本公告。"],
            },
        )
    )

    assert result.task_id
    assert result.call_ids
    task = AuditQueryStore().get_task(result.task_id)
    assert task is not None
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_07_qwen_failure_keeps_failed_audit_task(
    audit_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audit_database

    async def fake_build_selected(self, **kwargs):
        del self, kwargs
        return _announcement_bundle()

    async def failing_invoke(self, **kwargs):
        del self, kwargs
        raise RuntimeError("PROVIDER_ERROR")

    monkeypatch.setattr(
        "router.services.announcement_pipeline.AnnouncementEvidenceService.build_selected",
        fake_build_selected,
    )
    monkeypatch.setattr(
        "router.providers.qwen.QwenProvider.invoke",
        failing_invoke,
    )

    request = SimpleNamespace(
        symbol="600172.SH",
        record_id="record_test",
        announcement_id=None,
        start_date=None,
        end_date=None,
        claims=["公司发布了本公告。"],
        max_tokens=1000,
        model_dump=lambda **kwargs: {
            "symbol": "600172.SH",
            "record_id": "record_test",
            "claims": ["公司发布了本公告。"],
        },
    )
    with pytest.raises(RuntimeError, match="PROVIDER_ERROR"):
        await AnnouncementVerificationPipelineService().run(request)

    tasks = AuditQueryStore().list_tasks(limit=10)
    assert tasks.count == 1
    assert tasks.tasks[0].status == "failed"
    assert tasks.tasks[0].model_call_count == 1


def test_08_audit_list_returns_success_failed_and_running(
    audit_database: Path,
) -> None:
    del audit_database
    store = AuditStore()
    for status in ("completed", "failed", "running"):
        store.create_task(
            task_type="test",
            request_payload={},
            status=status,
        )
    result = AuditQueryStore().list_tasks(limit=10)
    assert {task.status for task in result.tasks} == {
        "completed",
        "failed",
        "running",
    }


def test_09_empty_audit_list_returns_http_200(
    audit_database: Path,
) -> None:
    del audit_database
    response = TestClient(app).get("/v1/audit/tasks")
    assert response.status_code == 200
    assert response.json() == {
        "count": 0,
        "tasks": [],
        "invalid_record_count": 0,
    }


def test_10_invalid_audit_row_does_not_fail_whole_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    rows = [
        ("good", "test", None, "completed", now, now, 1, 1),
        ("bad", "test", None, "failed", None, now, 0, 0),
    ]

    class Connection:
        def execute(self, *args, **kwargs):
            del args, kwargs
            return self

        def fetchall(self):
            return rows

        def close(self):
            return None

    monkeypatch.setattr(
        "router.services.audit_query.get_connection",
        lambda **kwargs: Connection(),
    )
    result = AuditQueryStore().list_tasks(limit=10)
    assert result.count == 1
    assert result.invalid_record_count == 1


def test_11_installed_mode_credential_is_injected_in_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = AppPaths.resolve(
        development=False,
        application_root=tmp_path / "program",
        user_data_override=tmp_path / "user-data",
        env={"LOCALAPPDATA": str(tmp_path / "local")},
    )
    paths.ensure_user_directories()
    desktop_settings = DesktopSettings()
    desktop_settings.mark_credential("longcat", "longcat", configured=True)
    desktop_settings.save(paths.config_dir / "desktop-settings.json")
    store = MemoryCredentialStore()
    store.write("longcat", "synthetic-test-credential")
    monkeypatch.setattr(router_settings, "longcat_api_key", None)

    activated = activate_configured_credentials(paths, store=store)
    child = child_process_environment(store, ["longcat"], base={})

    assert activated == ("longcat",)
    assert router_settings.longcat_api_key is not None
    assert bool(child.get("LONGCAT_API_KEY")) is True


def test_12_model_stage_log_has_fixed_fields_and_no_credential(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test.model_chain")
    with caplog.at_level(logging.INFO, logger=logger.name):
        payload = log_model_chain_stage(
            logger=logger,
            request_id="req_" + "c" * 24,
            skill_id="stock_research",
            provider_id="longcat",
            provider_registered=True,
            credential_loaded=True,
            request_started=True,
            response_received=False,
            response_status=None,
            audit_task_created=True,
            error_code="PROVIDER_ERROR",
            sanitized_error="LONGCAT_API_KEY=synthetic-sensitive-value",
        )
    assert tuple(payload) == MODEL_CHAIN_FIELDS
    assert "synthetic-sensitive-value" not in caplog.text


def test_13_formal_weights_remain_60_40() -> None:
    assert FORMAL_WEIGHTS == {
        FactorType.TECHNICAL: 0.60,
        FactorType.FUNDAMENTAL: 0.40,
    }


def test_14_hard_veto_remains_final() -> None:
    result = formal_result(
        DecisionShadowRequest(
            symbol="300750.SZ",
            data_cutoff=datetime.now().astimezone(),
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
    assert result.hard_veto is True


def test_15_pymupdf_and_package_guard_are_present() -> None:
    import pymupdf

    assert callable(pymupdf.open)
    root = Path(__file__).resolve().parents[1]
    spec = (root / "build" / "pyinstaller" / "hermes_opc.spec").read_text(
        encoding="utf-8"
    )
    verifier = (
        root / "build" / "scripts" / "verify_portable.py"
    ).read_text(encoding="utf-8")
    assert 'collect_submodules("pymupdf")' in spec
    assert "pymupdf_ready" in verifier


def test_16_stale_verified_service_is_stopped_before_restart(
    tmp_path: Path,
) -> None:
    paths = AppPaths.resolve(
        development=False,
        application_root=tmp_path / "program",
        user_data_override=tmp_path / "user-data",
        env={"LOCALAPPDATA": str(tmp_path / "local")},
    )
    paths.ensure_user_directories()
    executable = paths.executable_dir / "hermes-opc-service.exe"
    started = datetime(2026, 7, 31, tzinfo=timezone.utc)
    terminated: list[int] = []
    probes = iter([False, False, True])

    class Process:
        pid = 222

        def poll(self):
            return None

    supervisor = ServiceSupervisor(
        paths,
        process_factory=lambda *args, **kwargs: Process(),
        port_probe=lambda port: next(probes),
        process_probe=lambda pid: True,
        identity_probe=lambda pid: (str(executable), started),
        port_owner_probe=lambda port: None,
        pid_terminator=lambda pid, timeout: terminated.append(pid) or True,
    )
    supervisor._save_records(
        {
            "router": ServiceRecord(
                "router",
                111,
                str(executable),
                started.isoformat(),
                8765,
                "scripts.run_router_api",
            )
        }
    )

    status = supervisor.start("router", timeout=1)

    assert terminated == [111]
    assert status.state == "RUNNING"
