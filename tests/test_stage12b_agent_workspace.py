from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from desktop.workspace.models import TaskStatus, TimelineKind
from desktop.workspace.pages import ConversationPage, SkillCenterPage
from desktop.workspace.query_plan import IncrementalQueryPlanModifier
from desktop.workspace.service import TaskCenterService
from desktop.workspace.state import DesktopStateRepository
from router.integration.skills import (
    SKILL_REGISTRY,
    SkillResultCard,
    skill_catalog_payload,
)
from router.integration.wecom import LocalWeComAdapter
from router.integration.tools import HERMES_SKILL_REGISTRY
from router.integration.workflow import new_request_id
from trading.scanner.schemas import ScannerParseRequest, ScannerQueryPlan
from trading.scanner.service import MarketScannerService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repository(tmp_path: Path) -> DesktopStateRepository:
    return DesktopStateRepository(tmp_path / "用户 状态" / "desktop_state.sqlite3")


@pytest.fixture()
def service(repository: DesktopStateRepository) -> TaskCenterService:
    return TaskCenterService(repository)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def initial_plan() -> ScannerQueryPlan:
    response = MarketScannerService().parse(
        ScannerParseRequest(
            query="筛选10到20元、成交额超过5亿、排除ST的股票",
            data_cutoff=datetime.now().astimezone(),
            allow_parser_model=False,
        )
    )
    assert response.parsed_query is not None
    return response.parsed_query


def test_01_state_database_schema_is_independent_sqlite(
    repository: DesktopStateRepository,
) -> None:
    assert repository.path.suffix == ".sqlite3"
    with sqlite3.connect(repository.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "conversations",
        "messages",
        "conversation_contexts",
        "context_audits",
        "task_runs",
        "task_steps",
        "saved_task_templates",
    } <= tables


def test_02_conversation_create_rename_search_and_pin(
    repository: DesktopStateRepository,
) -> None:
    conversation = repository.create_conversation("研究会话")
    repository.rename_conversation(conversation.conversation_id, "宁德时代研究")
    repository.pin_conversation(conversation.conversation_id, True)
    found = repository.list_conversations("宁德")
    assert len(found) == 1
    assert found[0].title == "宁德时代研究"
    assert found[0].pinned is True


def test_03_multi_turn_stock_context(service: TaskCenterService) -> None:
    conversation = service.new_conversation()
    first = service.submit_message(conversation.conversation_id, "分析宁德时代")
    second = service.submit_message(
        conversation.conversation_id, "再看看最新公告"
    )
    third = service.submit_message(conversation.conversation_id, "和比亚迪对比")
    context = service.repository.context(conversation.conversation_id)
    assert first.skill_id == "stock_research"
    assert second.skill_id == "announcement_verification"
    assert third.skill_id == "stock_comparison"
    assert context.active_symbol == "300750.SZ"
    assert context.compared_symbols == ["002594.SZ"]


def test_skill_center_multi_symbol_research_uses_every_explicit_symbol(
    service: TaskCenterService,
) -> None:
    conversation = service.new_conversation()
    task = service.submit_message(
        conversation.conversation_id,
        "深度研究 300750.SZ 002594.SZ",
        skill_id_override="stock_research",
    )

    assert task.status == TaskStatus.COMPLETED
    assert task.result_card is not None
    summaries = task.result_card.fields["research_summaries"]
    assert [item["symbol"] for item in summaries] == [
        "300750.SZ",
        "002594.SZ",
    ]


def test_04_conversation_contexts_are_isolated(
    service: TaskCenterService,
) -> None:
    left = service.new_conversation()
    right = service.new_conversation()
    service.submit_message(left.conversation_id, "分析宁德时代")
    service.submit_message(right.conversation_id, "分析比亚迪")
    assert service.repository.context(left.conversation_id).active_symbol == "300750.SZ"
    assert service.repository.context(right.conversation_id).active_symbol == "002594.SZ"


def test_05_context_changes_are_audited(service: TaskCenterService) -> None:
    conversation = service.new_conversation()
    service.submit_message(conversation.conversation_id, "分析宁德时代")
    assert service.repository.context_audit_count(conversation.conversation_id) >= 2


def test_06_hidden_reasoning_is_not_saved(
    repository: DesktopStateRepository,
) -> None:
    conversation = repository.create_conversation()
    repository.add_message(
        conversation.conversation_id,
        "SYSTEM",
        "<analysis>private draft</analysis>公开结论",
    )
    stored = repository.messages(conversation.conversation_id)[0].content
    assert "private draft" not in stored
    assert "INTERNAL_REASONING_REMOVED" in stored


def test_07_token_fields_cannot_enter_task_state(
    repository: DesktopStateRepository,
) -> None:
    conversation = repository.create_conversation()
    with pytest.raises(ValueError, match="forbidden"):
        repository.create_task(
            conversation_id=conversation.conversation_id,
            request_id=new_request_id(),
            skill_id="system_doctor",
            idempotency_key="secret-test",
            input_payload={"token": "synthetic-test-value"},
        )
    assert "synthetic-test-value" not in repository.path.read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_08_sql_cannot_enter_task_timeline(
    repository: DesktopStateRepository,
) -> None:
    conversation = repository.create_conversation()
    task, _ = repository.create_task(
        conversation_id=conversation.conversation_id,
        request_id=new_request_id(),
        skill_id="system_doctor",
        idempotency_key="sql-test",
        input_payload={"user_input": "系统诊断"},
    )
    with pytest.raises(ValueError, match="SQL"):
        repository.add_step(
            task.task_id,
            TimelineKind.DATA_READ,
            "数据读取",
            {"statement": "SELECT * FROM private_table"},
        )


def test_09_same_task_idempotency_key_returns_existing(
    service: TaskCenterService,
) -> None:
    conversation = service.new_conversation()
    left = service.submit_message(
        conversation.conversation_id,
        "分析宁德时代",
        idempotency_key="same-request",
    )
    right = service.submit_message(
        conversation.conversation_id,
        "分析宁德时代",
        idempotency_key="same-request",
    )
    assert left.task_id == right.task_id
    assert len(service.repository.messages(conversation.conversation_id)) == 2


def test_10_pending_task_can_be_cancelled(
    repository: DesktopStateRepository,
) -> None:
    conversation = repository.create_conversation()
    task, _ = repository.create_task(
        conversation_id=conversation.conversation_id,
        request_id=new_request_id(),
        skill_id="system_doctor",
        idempotency_key="cancel",
        input_payload={"user_input": "系统诊断"},
    )
    assert repository.request_cancel(task.task_id).status == TaskStatus.CANCELLED


def test_11_failed_task_can_be_retried(
    service: TaskCenterService,
) -> None:
    conversation = service.new_conversation()
    task, _ = service.repository.create_task(
        conversation_id=conversation.conversation_id,
        request_id=new_request_id(),
        skill_id="stock_research",
        idempotency_key="failed",
        input_payload={
            "user_input": "分析宁德时代",
            "data_cutoff": datetime.now().astimezone().isoformat(),
        },
    )
    service.repository.set_task_status(task.task_id, TaskStatus.FAILED)
    retried = service.retry(task.task_id)
    assert retried.parent_task_id == task.task_id
    assert retried.status == TaskStatus.COMPLETED


def test_12_decision_waits_for_exact_confirmation(
    service: TaskCenterService,
) -> None:
    conversation = service.new_conversation()
    task = service.submit_message(
        conversation.conversation_id, "对宁德时代正式决策"
    )
    assert task.status == TaskStatus.WAITING_CONFIRMATION
    assert task.result_card is not None
    assert task.result_card.fields["decision_called"] is False
    assert service.repository.context(
        conversation.conversation_id
    ).pending_confirmation == "CONFIRM_DECISION:300750.SZ"


def test_13_exact_decision_confirmation_continues_same_context(
    service: TaskCenterService,
) -> None:
    conversation = service.new_conversation()
    service.submit_message(conversation.conversation_id, "对宁德时代正式决策")
    confirmed = service.submit_message(
        conversation.conversation_id, "CONFIRM_DECISION:300750.SZ"
    )
    assert confirmed.status == TaskStatus.PARTIAL
    assert confirmed.result_card is not None
    assert confirmed.result_card.fields["decision_called"] is True
    assert confirmed.result_card.fields["order_created"] is False
    assert (
        confirmed.result_card.fields["formal_result"]["status"]
        == "INSUFFICIENT_COVERAGE"
    )


def test_14_skill_registry_rejects_unconfirmed_decision() -> None:
    payload = {
        "request_id": new_request_id(),
        "data_cutoff": datetime.now().astimezone(),
        "symbols": ["300750.SZ"],
    }
    with pytest.raises(ValueError, match="confirmation"):
        SKILL_REGISTRY.validate_input("decision_support", payload)


def test_15_manual_trade_requires_two_stage_confirmation() -> None:
    preview = {
        "request_id": new_request_id(),
        "data_cutoff": datetime.now().astimezone(),
        "symbols": ["300750.SZ"],
        "payload": {"operation": "PREVIEW"},
    }
    assert (
        SKILL_REGISTRY.validate_input("manual_trade_tracking", preview).payload[
            "operation"
        ]
        == "PREVIEW"
    )
    confirmation = {
        **preview,
        "request_id": new_request_id(),
        "payload": {"operation": "CONFIRM", "preview_id": "preview_1"},
        "confirmation": "wrong",
    }
    with pytest.raises(ValueError, match="confirmation"):
        SKILL_REGISTRY.validate_input("manual_trade_tracking", confirmation)


def test_16_scanner_never_auto_enters_decision(
    service: TaskCenterService,
) -> None:
    conversation = service.new_conversation()
    task = service.submit_message(
        conversation.conversation_id,
        "筛选10到20元、成交额超过5亿、排除ST的股票",
    )
    assert task.skill_id == "market_scanner"
    assert task.result_card is not None
    assert task.result_card.fields["network_request_count"] == 0
    assert task.result_card.fields["model_call_count"] == 0
    assert task.result_card.fields["decision_called"] is False


def test_17_price_range_increment_preserves_other_conditions() -> None:
    original = initial_plan()
    result = IncrementalQueryPlanModifier().modify(original, "改成20到30元")
    updated = ScannerQueryPlan.model_validate(result.plan)
    assert updated.plan_hash != original.plan_hash
    assert updated.top_n == original.top_n
    assert any(change.field == "filters.current_price_range" for change in result.changes)
    assert "500000000" in json.dumps(updated.model_dump(mode="json"))


def test_18_board_exclusion_is_incremental() -> None:
    original = initial_plan()
    result = IncrementalQueryPlanModifier().modify(original, "再排除创业板")
    updated = ScannerQueryPlan.model_validate(result.plan)
    assert updated.exclude_boards == [*original.exclude_boards, "CHINEXT"]
    assert updated.filters == original.filters


def test_19_industry_filter_is_incremental() -> None:
    result = IncrementalQueryPlanModifier().modify(initial_plan(), "只看半导体")
    assert result.plan is not None
    assert result.plan["include_industries"] == ["半导体"]


def test_20_top_n_change_is_incremental() -> None:
    result = IncrementalQueryPlanModifier().modify(
        initial_plan(), "前20只改成前10只"
    )
    assert result.plan is not None
    assert result.plan["top_n"] == 10


def test_21_sort_change_is_incremental() -> None:
    result = IncrementalQueryPlanModifier().modify(initial_plan(), "按量比排序")
    assert result.plan is not None
    assert result.plan["sort_fields"][0]["field"] == "volume_ratio_5d"


def test_22_ambiguous_plan_change_requests_clarification() -> None:
    result = IncrementalQueryPlanModifier().modify(initial_plan(), "换一个")
    assert result.clarification_required is True
    assert result.plan is None


def test_23_registry_has_all_required_skills() -> None:
    required = {
        "market_scanner",
        "stock_research",
        "stock_comparison",
        "announcement_verification",
        "news_impact_analysis",
        "decision_support",
        "watchlist_management",
        "position_risk_review",
        "backtest",
        "paper_account_review",
        "manual_trade_tracking",
        "daily_review",
        "premarket_brief",
        "experiment_report",
        "system_doctor",
    }
    assert required <= {item.skill_id for item in SKILL_REGISTRY.list()}
    assert len(SKILL_REGISTRY.list()) == 16


def test_24_skill_input_and_output_schemas_validate() -> None:
    contract = SKILL_REGISTRY.get("stock_research")
    assert contract.input_schema
    assert contract.output_schema
    invocation = SKILL_REGISTRY.validate_input(
        "stock_research",
        {
            "request_id": new_request_id(),
            "data_cutoff": datetime.now().astimezone(),
            "symbols": ["300750.SZ"],
        },
    )
    assert invocation.symbols == ["300750.SZ"]
    with pytest.raises(ValidationError):
        SkillResultCard.model_validate(
            {
                "card_type": "x",
                "title": "x",
                "status": "SUCCESS",
                "request_id": new_request_id(),
                "data_cutoff": datetime.now().astimezone(),
                "order_created": True,
            }
        )


def test_25_desktop_hermes_and_wecom_share_registry(
    repository: DesktopStateRepository,
) -> None:
    desktop = TaskCenterService(repository)
    wecom = LocalWeComAdapter()
    assert desktop.skill_registry is SKILL_REGISTRY
    assert wecom.skill_registry is SKILL_REGISTRY
    assert HERMES_SKILL_REGISTRY is SKILL_REGISTRY
    assert skill_catalog_payload()["skill_count"] == 16


def test_26_result_card_is_structured_and_request_id_is_continuous(
    service: TaskCenterService,
) -> None:
    conversation = service.new_conversation()
    task = service.submit_message(conversation.conversation_id, "分析宁德时代")
    assert task.result_card is not None
    assert task.result_card.request_id == task.request_id
    assert all(
        step.payload.get("request_id", task.request_id) == task.request_id
        for step in service.repository.steps(task.task_id)
    )


def test_27_deleting_conversation_does_not_delete_market_facts(
    repository: DesktopStateRepository,
) -> None:
    """删除会话不得删除市场数据（before==after 是关键断言）

    注意：data_records 会随正常数据刷新增长，不能用固定总数断言。
    验证删除会话前后数据量不变（会话数据与市场数据隔离）。
    """
    market_database = PROJECT_ROOT / "database" / "hermes_opc.duckdb"
    with duckdb.connect(str(market_database), read_only=True) as connection:
        before = int(connection.execute("SELECT count(*) FROM data_records").fetchone()[0])
    conversation = repository.create_conversation()
    assert repository.delete_conversation(conversation.conversation_id) is True
    with duckdb.connect(str(market_database), read_only=True) as connection:
        after = int(connection.execute("SELECT count(*) FROM data_records").fetchone()[0])
    # 删除会话前后市场数据量不变（隔离生效）
    assert before == after, f"删除会话影响了市场数据: before={before}, after={after}"


def test_28_export_contains_only_visible_messages(
    service: TaskCenterService, tmp_path: Path
) -> None:
    conversation = service.new_conversation()
    service.submit_message(conversation.conversation_id, "分析宁德时代")
    destination = service.export_conversation(
        conversation.conversation_id, tmp_path / "导出 报告.md"
    )
    text = destination.read_text(encoding="utf-8")
    assert "分析宁德时代" in text
    assert "chain-of-thought" not in text.casefold()
    assert "<analysis>" not in text.casefold()


def test_29_conversation_and_skill_pages_are_real(
    qt_app: QApplication,
    repository: DesktopStateRepository,
    service: TaskCenterService,
    tmp_path: Path,
) -> None:
    conversation_page = ConversationPage(
        repository, service, exports_dir=tmp_path / "exports"
    )
    skill_page = SkillCenterPage()
    assert conversation_page.conversations.count() >= 1
    assert skill_page.skills.count() == 16
    conversation_page.close()
    skill_page.close()


def test_30_no_agent_workspace_migration_0112_created() -> None:
    migrations = PROJECT_ROOT / "database" / "migrations"
    assert not list(migrations.glob("v0112_agent_workspace_v1.py"))
