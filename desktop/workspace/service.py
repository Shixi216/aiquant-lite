from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from desktop.workspace.models import (
    QueryPlanModification,
    TaskRun,
    TaskStatus,
    TimelineKind,
)
from desktop.workspace.model_research import ResearchModelClient
from desktop.workspace.query_plan import IncrementalQueryPlanModifier
from desktop.workspace.safety import sanitize_user_visible_text
from desktop.workspace.state import DesktopStateRepository
from router.integration.skills import (
    SKILL_REGISTRY,
    SkillRegistry,
    SkillResultCard,
)
from router.integration.identifiers import new_request_id
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    ResearchRequest,
)
from trading.research.orchestration.service import OrchestrationService
from trading.scanner.schemas import (
    ScannerParseRequest,
    ScannerQueryPlan,
    ScannerScanRequest,
)
from trading.scanner.service import MarketScannerService
from trading.schemas import AnalysisMode


KNOWN_SYMBOLS = {
    "宁德时代": "300750.SZ",
    "比亚迪": "002594.SZ",
    "浦发银行": "600000.SH",
}


def _symbol_from_text(text: str) -> str | None:
    symbols = _symbols_from_text(text)
    return symbols[0] if symbols else None


def _symbols_from_text(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for name, symbol in KNOWN_SYMBOLS.items():
        start = text.find(name)
        if start >= 0:
            found.append((start, symbol))
    found.extend(
        (match.start(), match.group(0))
        for match in __import__("re").finditer(
            r"\b\d{6}\.(?:SH|SZ|BJ)\b",
            text.upper(),
        )
    )
    return list(dict.fromkeys(symbol for _, symbol in sorted(found)))


class TaskCenterService:
    def __init__(
        self,
        repository: DesktopStateRepository,
        *,
        skill_registry: SkillRegistry = SKILL_REGISTRY,
        scanner_factory: Any = MarketScannerService,
        orchestration_factory: Any = OrchestrationService,
        research_model_client: ResearchModelClient | None = None,
    ) -> None:
        self.repository = repository
        self.skill_registry = skill_registry
        self.scanner_factory = scanner_factory
        self.orchestration_factory = orchestration_factory
        self.research_model_client = research_model_client
        self.plan_modifier = IncrementalQueryPlanModifier()

    def new_conversation(self, title: str = "新会话"):
        return self.repository.create_conversation(title)

    def submit_message(
        self,
        conversation_id: str,
        text: str,
        *,
        idempotency_key: str | None = None,
        skill_id_override: str | None = None,
        on_created: Callable[[TaskRun], None] | None = None,
    ) -> TaskRun:
        clean_text = sanitize_user_visible_text(text).strip()
        if not clean_text:
            raise ValueError("message must not be empty")
        context = self.repository.context(conversation_id)
        request_id = new_request_id()
        skill_id = skill_id_override or self._select_skill(clean_text, context)
        self.skill_registry.get(skill_id)
        key = idempotency_key or hashlib.sha256(
            f"{conversation_id}|{clean_text}|{context.current_task_id}".encode()
        ).hexdigest()
        task, created = self.repository.create_task(
            conversation_id=conversation_id,
            request_id=request_id,
            skill_id=skill_id,
            idempotency_key=key,
            input_payload={
                "user_input": clean_text,
                "data_cutoff": context.data_cutoff.isoformat(),
            },
        )
        if on_created is not None:
            on_created(task)
        if not created:
            return task
        message = self.repository.add_message(
            conversation_id,
            "USER",
            clean_text,
            request_id=request_id,
        )
        self.repository.add_step(
            task.task_id,
            TimelineKind.USER_INPUT,
            "收到用户输入",
            {"message_id": message.message_id},
        )
        self.repository.update_context(
            conversation_id,
            {
                "user_message_id": message.message_id,
                "current_skill": skill_id,
                "current_task_id": task.task_id,
            },
            actor="USER",
        )
        try:
            return self._execute(task, clean_text)
        except Exception as error:
            error_code, sanitized_reason = self._classify_error(error)
            self.repository.add_step(
                task.task_id,
                TimelineKind.FAILED,
                "任务执行失败",
                {
                    "error_code": error_code,
                    "sanitized_reason": sanitized_reason,
                },
            )
            return self.repository.set_task_status(
                task.task_id,
                TaskStatus.FAILED,
                error_code=error_code,
            )

    def _execute(self, task: TaskRun, text: str) -> TaskRun:
        cancelled = self._cancel_if_requested(task)
        if cancelled is not None:
            return cancelled
        self.repository.set_task_status(task.task_id, TaskStatus.RUNNING)
        self.repository.add_step(
            task.task_id,
            TimelineKind.SKILL_SELECTED,
            f"选择技能：{task.skill_id}",
            {"skill_id": task.skill_id, "request_id": task.request_id},
        )
        context = self.repository.context(task.conversation_id)
        requested_symbols = _symbols_from_text(text)
        symbol = (
            requested_symbols[0]
            if requested_symbols
            else context.active_symbol
        )
        fields: dict[str, Any] = {}
        status = "SUCCESS"
        risk_flags = ["RESEARCH_ONLY"]
        warnings: list[str] = []

        if task.skill_id == "market_scanner":
            scanner = self.scanner_factory()
            parsed = scanner.parse(
                ScannerParseRequest(
                    query=text,
                    data_cutoff=context.data_cutoff,
                    allow_parser_model=False,
                )
            )
            self.repository.add_step(
                task.task_id,
                TimelineKind.QUERY_PARSE,
                "本地解析查询计划",
                {"model_call_count": parsed.parser_model_call_count},
            )
            if parsed.clarification_required or parsed.parsed_query is None:
                fields = {
                    "clarification_questions": parsed.clarification_questions,
                    "unsupported_fragments": parsed.unsupported_fragments,
                }
                status = "DEGRADED"
                self.repository.set_task_status(
                    task.task_id, TaskStatus.WAITING_DATA
                )
            else:
                plan_payload = parsed.parsed_query.model_dump(mode="json")
                scan = scanner.scan(
                    ScannerScanRequest(
                        query=text,
                        analysis_mode=AnalysisMode.SCREENING,
                        data_cutoff=context.data_cutoff,
                        top_n=20,
                        allow_parser_model=False,
                        persist_run=False,
                    )
                )
                self.repository.update_context(
                    task.conversation_id,
                    {
                        "last_scanner_query_plan": plan_payload,
                        "analysis_mode": "SCREENING",
                    },
                )
                fields = {
                    "plan": plan_payload,
                    "condition_summary": parsed.condition_summary,
                    "universe_count": scan.universe_count,
                    "scanned_count": scan.scanned_count,
                    "matched_count": scan.matched_count,
                    "returned_count": scan.returned_count,
                    "snapshot_time": scan.snapshot_time,
                    "stale": scan.stale,
                    "candidate_symbols": [
                        item.symbol for item in scan.candidates
                    ],
                    "network_request_count": scan.network_request_count,
                    "model_call_count": scan.model_call_count,
                    "decision_called": False,
                }
                risk_flags = ["NOT_A_TRADE_RECOMMENDATION"]
        elif task.skill_id == "scanner_plan_modification":
            if context.last_scanner_query_plan is None:
                modification = QueryPlanModification(
                    plan=None,
                    changes=[],
                    clarification_required=True,
                    clarification_question="当前会话没有可修改的筛选计划。",
                )
            else:
                modification = self.plan_modifier.modify(
                    ScannerQueryPlan.model_validate(
                        context.last_scanner_query_plan
                    ),
                    text,
                )
            fields = modification.model_dump(mode="json")
            if modification.clarification_required:
                status = "DEGRADED"
                self.repository.set_task_status(
                    task.task_id, TaskStatus.WAITING_DATA
                )
            else:
                self.repository.update_context(
                    task.conversation_id,
                    {"last_scanner_query_plan": modification.plan},
                )
        elif task.skill_id == "decision_support":
            if not symbol:
                status = "DEGRADED"
                fields = {"missing": "active_symbol"}
                self.repository.set_task_status(
                    task.task_id, TaskStatus.WAITING_DATA
                )
            else:
                expected = f"CONFIRM_DECISION:{symbol}"
                if text != expected:
                    self.repository.update_context(
                        task.conversation_id,
                        {
                            "active_symbol": symbol,
                            "pending_confirmation": expected,
                            "analysis_mode": "DECISION",
                        },
                    )
                    fields = {
                        "symbol": symbol,
                        "required_confirmation": expected,
                        "decision_called": False,
                        "order_created": False,
                    }
                    status = "WAITING_CONFIRMATION"
                    risk_flags = ["CONFIRMATION_REQUIRED", "NO_ORDER_CREATED"]
                    self.repository.add_step(
                        task.task_id,
                        TimelineKind.WAITING_CONFIRMATION,
                        "等待正式决策显式确认",
                        {"symbol": symbol},
                    )
                    card = self._card(
                        task,
                        status,
                        fields,
                        warnings,
                        risk_flags,
                    )
                    self.repository.add_message(
                        task.conversation_id,
                        "SYSTEM",
                        f"需要显式确认：{expected}",
                        request_id=task.request_id,
                    )
                    return self.repository.set_task_status(
                        task.task_id,
                        TaskStatus.WAITING_CONFIRMATION,
                        result_card=card,
                    )
                self.skill_registry.validate_input(
                    "decision_support",
                    {
                        "request_id": task.request_id,
                        "conversation_id": task.conversation_id,
                        "data_cutoff": context.data_cutoff,
                        "symbols": [symbol],
                        "confirmation": text,
                    },
                )
                self.repository.update_context(
                    task.conversation_id,
                    {"pending_confirmation": None},
                )
                orchestration = self.orchestration_factory()
                symbol_view = orchestration.symbol(
                    symbol=symbol,
                    data_cutoff=context.data_cutoff,
                    analysis_mode=AnalysisMode.RESEARCH,
                )
                technical = symbol_view.bundle.technical
                fundamental = symbol_view.bundle.fundamental
                decision = orchestration.decision_shadow(
                    DecisionShadowRequest(
                        symbol=symbol,
                        data_cutoff=context.data_cutoff,
                        technical_score=technical.score if technical else 0.0,
                        technical_confidence=(
                            technical.confidence if technical else 0.0
                        ),
                        fundamental_score=(
                            fundamental.score if fundamental else 0.0
                        ),
                        fundamental_confidence=(
                            fundamental.confidence if fundamental else 0.0
                        ),
                        persist_shadow=False,
                    )
                )
                missing = [item.value for item in decision.missing_factors]
                formal = decision.formal_result.model_dump(mode="json")
                if "FUNDAMENTAL" in missing:
                    formal = {
                        "status": "INSUFFICIENT_COVERAGE",
                        "proposal_action": None,
                        "final_action": None,
                        "hard_veto": decision.formal_result.hard_veto,
                        "formal_weights": decision.formal_weights,
                    }
                    status = "DEGRADED"
                    warnings.append("INSUFFICIENT_FUNDAMENTAL_COVERAGE")
                else:
                    formal["status"] = "AVAILABLE"
                fields = {
                    "symbol": symbol,
                    "confirmation_accepted": True,
                    "decision_called": True,
                    "decision_packet_created": False,
                    "order_created": False,
                    "formal_result": formal,
                    "formal_weights": decision.formal_weights,
                    "missing_factors": missing,
                    "shadow_formal_weight": 0,
                }
                risk_flags = ["NO_ORDER_CREATED", "HARD_VETO_FINAL"]
        elif task.skill_id == "manual_trade_tracking":
            fields = {
                "phase": "PREVIEW",
                "confirmation_required": True,
                "ledger_written": False,
                "broker_execution": False,
            }
            status = "WAITING_CONFIRMATION"
            risk_flags = ["MANUAL_LEDGER_ONLY", "NO_BROKER_EXECUTION"]
            card = self._card(task, status, fields, warnings, risk_flags)
            return self.repository.set_task_status(
                task.task_id,
                TaskStatus.WAITING_CONFIRMATION,
                result_card=card,
            )
        elif task.skill_id == "watchlist_management":
            if not symbol:
                status = "DEGRADED"
                fields = {"missing": "active_symbol"}
                self.repository.set_task_status(
                    task.task_id, TaskStatus.WAITING_DATA
                )
            else:
                from desktop.workspace.watchlists import WatchlistRepository

                watchlists = WatchlistRepository(self.repository)
                available = watchlists.list_watchlists()
                watchlist = (
                    available[0]
                    if available
                    else watchlists.create("默认观察池")
                )
                existing = {
                    item.symbol for item in watchlists.items(watchlist.watchlist_id)
                }
                added = symbol not in existing
                if added:
                    watchlists.add_symbol(
                        watchlist.watchlist_id,
                        symbol,
                        tags=["对话加入"],
                        source_task_id=task.task_id,
                    )
                self.repository.update_context(
                    task.conversation_id,
                    {
                        "active_symbol": symbol,
                        "selected_candidates": list(
                            dict.fromkeys(
                                [*context.selected_candidates, symbol]
                            )
                        ),
                    },
                )
                fields = {
                    "watchlist_id": watchlist.watchlist_id,
                    "watchlist_name": watchlist.name,
                    "symbol": symbol,
                    "added": added,
                    "is_position": False,
                    "order_created": False,
                }
                risk_flags = ["WATCHLIST_ONLY", "NOT_A_POSITION"]
        elif task.skill_id in {
            "announcement_verification",
            "news_impact_analysis",
            "position_risk_review",
            "paper_account_review",
            "daily_review",
            "premarket_brief",
            "experiment_report",
            "system_doctor",
        }:
            fields, skill_warnings = self._local_workspace_result(
                task.skill_id,
                symbol=symbol,
                data_cutoff=context.data_cutoff,
            )
            warnings.extend(skill_warnings)
            if skill_warnings:
                status = "DEGRADED"
        elif task.skill_id == "backtest":
            from desktop.workspace.backtests import (
                BacktestWorkspaceConfig,
                BacktestWorkspaceService,
            )

            backtests = BacktestWorkspaceService()
            experiment_id = backtests.latest_experiment_id()
            if experiment_id is None:
                status = "DEGRADED"
                warnings.append("EXPERIMENT_RUN_NOT_FOUND")
                fields = {"experiment_id": None, "persisted": False}
            else:
                result = backtests.run_existing(
                    BacktestWorkspaceConfig(
                        experiment_id=experiment_id,
                        symbols=["EXPERIMENT_UNIVERSE"],
                        start_date=context.data_cutoff.date()
                        - timedelta(days=365),
                        end_date=context.data_cutoff.date(),
                    )
                )
                fields = {
                    "experiment_id": experiment_id,
                    "status": result.status,
                    "metrics": result.metrics,
                    "sample_count": result.data_quality.get(
                        "sample_count", 0
                    ),
                    "persisted": result.data_quality.get("persisted", False),
                    "manual_ledger_written": False,
                    "paper_trading_written": False,
                    "profitability_proven": False,
                }
                risk_flags = list(result.risk_flags)
        else:
            if symbol:
                context_updates: dict[str, Any] = {}
                if task.skill_id == "stock_comparison":
                    active_symbol = context.active_symbol
                    if active_symbol is None:
                        active_symbol = requested_symbols[0]
                        context_updates["active_symbol"] = active_symbol
                    compared = list(context.compared_symbols)
                    for item in requested_symbols:
                        if item != active_symbol and item not in compared:
                            compared.append(item)
                    context_updates["compared_symbols"] = compared
                else:
                    context_updates["active_symbol"] = symbol
                if task.skill_id == "watchlist_management":
                    selected = list(context.selected_candidates)
                    if symbol not in selected:
                        selected.append(symbol)
                    context_updates["selected_candidates"] = selected
                self.repository.update_context(
                    task.conversation_id, context_updates
                )
            latest_context = self.repository.context(task.conversation_id)
            if task.skill_id in {"stock_research", "stock_comparison"} and symbol:
                research_symbols = (
                    requested_symbols or [symbol]
                    if task.skill_id == "stock_research"
                    else list(
                        dict.fromkeys(
                            [
                                item
                                for item in (
                                    latest_context.active_symbol,
                                    *latest_context.compared_symbols,
                                )
                                if item
                            ]
                        )
                    )
                )
                self.repository.add_step(
                    task.task_id,
                    TimelineKind.TOOL_CALL,
                    "调用工具：OrchestrationService.research",
                    {"symbol_count": len(research_symbols)},
                )
                research = self.orchestration_factory().research(
                    ResearchRequest(
                        symbols=research_symbols,
                        data_cutoff=latest_context.data_cutoff,
                        persist=False,
                        allow_external_fetch=False,
                    )
                )
                cancelled = self._cancel_if_requested(task)
                if cancelled is not None:
                    return cancelled
                self.repository.add_step(
                    task.task_id,
                    TimelineKind.DATA_READ,
                    "读取本地点时五维研究数据",
                    {
                        "network_request_count": research.network_request_count,
                        "model_call_count": research.model_call_count,
                    },
                )
                model_results = []
                local_summaries = [
                    {
                        "symbol": item.symbol,
                        "factor_coverage": item.factor_coverage.display,
                        "available_factors": [
                            factor.value for factor in item.available_factors
                        ],
                        "missing_factors": [
                            factor.value for factor in item.missing_factors
                        ],
                        "risk_flags": [
                            flag.value for flag in item.risk_flags
                        ],
                    }
                    for item in research.results
                ]
                if (
                    task.skill_id == "stock_research"
                    and self.research_model_client is not None
                ):
                    for local_summary in local_summaries:
                        model_results.append(
                            self.research_model_client.synthesize(
                                request_id=task.request_id,
                                symbol=local_summary["symbol"],
                                data_cutoff=latest_context.data_cutoff,
                                local_research=local_summary,
                            )
                        )
                    self.repository.add_step(
                        task.task_id,
                        TimelineKind.TOOL_CALL,
                        "调用模型：research_synthesizer",
                        {
                            "providers": sorted(
                                {
                                    result.provider
                                    for result in model_results
                                }
                            ),
                            "model_call_count": sum(
                                result.model_call_count
                                for result in model_results
                            ),
                            "audit_task_created": True,
                        },
                    )
                fields = {
                    "active_symbol": latest_context.active_symbol,
                    "compared_symbols": latest_context.compared_symbols,
                    "research_summaries": local_summaries,
                    "network_request_count": research.network_request_count,
                    "model_call_count": (
                        sum(
                            result.model_call_count
                            for result in model_results
                        )
                        if model_results
                        else research.model_call_count
                    ),
                    "model_research": (
                        [
                            result.output.model_dump(mode="json")
                            for result in model_results
                        ]
                        if model_results
                        else None
                    ),
                    "model_provider": (
                        sorted(
                            {
                                result.provider
                                for result in model_results
                            }
                        )
                        if model_results
                        else None
                    ),
                    "model_audit_task_id": (
                        [
                            result.task_id
                            for result in model_results
                        ]
                        if model_results
                        else None
                    ),
                }
            else:
                fields = {
                    "active_symbol": symbol,
                    "compared_symbols": latest_context.compared_symbols,
                    "skill_id": task.skill_id,
                }

        card = self._card(task, status, fields, warnings, risk_flags)
        self.repository.add_step(
            task.task_id,
            TimelineKind.STRUCTURED_RESULT,
            "生成结构化结果卡片",
            {"card_type": card.card_type, "request_id": task.request_id},
        )
        final_status = (
            TaskStatus.PARTIAL if status == "DEGRADED" else TaskStatus.COMPLETED
        )
        self.repository.add_step(
            task.task_id,
            TimelineKind.COMPLETED,
            "任务完成" if final_status == TaskStatus.COMPLETED else "任务部分完成",
            {"status": final_status.value},
        )
        self.repository.add_message(
            task.conversation_id,
            "SYSTEM",
            f"{self.skill_registry.get(task.skill_id).name}：{status}",
            request_id=task.request_id,
        )
        return self.repository.set_task_status(
            task.task_id,
            final_status,
            result_card=card,
        )

    def _local_workspace_result(
        self,
        skill_id: str,
        *,
        symbol: str | None,
        data_cutoff,
    ) -> tuple[dict[str, Any], list[str]]:
        from database.db import get_connection

        warnings: list[str] = []
        with get_connection() as connection:
            if skill_id == "announcement_verification":
                count = 0
                if symbol:
                    count = int(
                        connection.execute(
                            """
                            SELECT count(*)
                            FROM data_records
                            WHERE symbol = ?
                              AND upper(data_type) LIKE '%ANNOUNC%'
                              AND event_time <= ?
                            """,
                            [symbol, data_cutoff],
                        ).fetchone()[0]
                    )
                warnings.append("MODEL_VERIFICATION_NOT_EXECUTED")
                return (
                    {
                        "symbol": symbol,
                        "local_announcement_count": count,
                        "source_evidence_checked": count > 0,
                        "model_call_count": 0,
                        "order_created": False,
                    },
                    warnings,
                )
            if skill_id == "news_impact_analysis":
                news_count = 0
                if symbol:
                    news_count = int(
                        connection.execute(
                            """
                            SELECT count(*)
                            FROM data_records
                            WHERE symbol = ?
                              AND upper(data_type) LIKE '%NEWS%'
                              AND event_time <= ?
                            """,
                            [symbol, data_cutoff],
                        ).fetchone()[0]
                    )
                warnings.append("MODEL_IMPACT_ANALYSIS_NOT_EXECUTED")
                return (
                    {
                        "symbol": symbol,
                        "local_news_count": news_count,
                        "model_call_count": 0,
                        "formal_weight": 0,
                        "order_created": False,
                    },
                    warnings,
                )
            if skill_id == "position_risk_review":
                trade_count = int(
                    connection.execute(
                        "SELECT count(*) FROM manual_trades"
                    ).fetchone()[0]
                )
                review_count = int(
                    connection.execute(
                        "SELECT count(*) FROM manual_position_risk_reviews"
                    ).fetchone()[0]
                )
                return (
                    {
                        "manual_trade_count": trade_count,
                        "risk_review_count": review_count,
                        "position_modified": False,
                        "order_created": False,
                    },
                    warnings,
                )
            if skill_id == "paper_account_review":
                account_count = int(
                    connection.execute(
                        "SELECT count(*) FROM paper_accounts"
                    ).fetchone()[0]
                )
                order_count = int(
                    connection.execute(
                        """
                        SELECT count(*) FROM trading_orders
                        WHERE mode = 'paper'
                        """
                    ).fetchone()[0]
                )
                return (
                    {
                        "paper_account_count": account_count,
                        "paper_order_count": order_count,
                        "manual_ledger_included": False,
                        "live_order_supported": False,
                    },
                    warnings,
                )
            if skill_id in {"daily_review", "premarket_brief"}:
                snapshot = connection.execute(
                    """
                    SELECT snapshot_time
                    FROM market_snapshot_runs
                    WHERE snapshot_time <= ?
                    ORDER BY snapshot_time DESC
                    LIMIT 1
                    """,
                    [data_cutoff],
                ).fetchone()
                return (
                    {
                        "report_type": skill_id.upper(),
                        "latest_snapshot_time": (
                            None if snapshot is None else snapshot[0]
                        ),
                        "data_cutoff": data_cutoff,
                        "order_created": False,
                        "trade_instruction_created": False,
                    },
                    warnings,
                )
            if skill_id == "experiment_report":
                counts = connection.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM experiment_definitions),
                        (SELECT count(*) FROM experiment_runs),
                        (SELECT count(*) FROM backtest_runs)
                    """
                ).fetchone()
                return (
                    {
                        "experiment_definitions": int(counts[0]),
                        "experiment_runs": int(counts[1]),
                        "backtest_runs": int(counts[2]),
                        "profitability_proven": False,
                    },
                    warnings,
                )
            table_count = int(
                connection.execute(
                    """
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_schema = 'main'
                    """
                ).fetchone()[0]
            )
            return (
                {
                    "database_readable": True,
                    "table_count": table_count,
                    "data_cutoff": data_cutoff,
                    "mutations_performed": False,
                    "live_trading_supported": False,
                },
                warnings,
            )

    @staticmethod
    def _classify_error(error: Exception) -> tuple[str, str]:
        message = str(error).strip()
        if __import__("re").fullmatch(r"[A-Z][A-Z0-9_]{2,63}", message):
            return message, "任务返回了稳定错误代码。"
        if isinstance(error, ModuleNotFoundError):
            return "COMPONENT_UNAVAILABLE", "安装包缺少必需运行组件。"
        if isinstance(error, OSError):
            return "IO_ERROR", "本地文件或数据库操作失败。"
        if isinstance(error, ValueError):
            return "INVALID_INPUT", "请求参数或数据校验失败。"
        return "TASK_FAILED", "任务执行失败，未生成交易指令。"

    def _cancel_if_requested(self, task: TaskRun) -> TaskRun | None:
        current = self.repository.task(task.task_id)
        if current.status != TaskStatus.CANCEL_REQUESTED:
            return None
        self.repository.add_step(
            task.task_id,
            TimelineKind.FAILED,
            "用户已取消任务",
            {"error_code": "CANCELLED_BY_USER"},
        )
        return self.repository.set_task_status(
            task.task_id,
            TaskStatus.CANCELLED,
            error_code="CANCELLED_BY_USER",
        )

    def _card(
        self,
        task: TaskRun,
        status: str,
        fields: dict[str, Any],
        warnings: list[str],
        risk_flags: list[str],
    ) -> SkillResultCard:
        payload = {
            "card_type": f"{task.skill_id}_result",
            "title": self.skill_registry.get(task.skill_id).name,
            "status": status,
            "request_id": task.request_id,
            "data_cutoff": self.repository.context(
                task.conversation_id
            ).data_cutoff,
            "fields": fields,
            "warnings": warnings,
            "risk_flags": risk_flags,
        }
        return self.skill_registry.validate_output(task.skill_id, payload)

    def _select_skill(self, text: str, context: Any) -> str:
        if context.pending_confirmation and text == context.pending_confirmation:
            return context.current_skill or "decision_support"
        if any(marker in text for marker in ("改成", "再排除", "只看", "排序")):
            return "scanner_plan_modification"
        if "正式决策" in text or text.startswith("CONFIRM_DECISION:"):
            return "decision_support"
        if "人工成交" in text or "记录成交" in text:
            return "manual_trade_tracking"
        if "公告" in text:
            return "announcement_verification"
        if "对比" in text or "比较" in text:
            return "stock_comparison"
        if "观察池" in text or "自选" in text:
            return "watchlist_management"
        if any(marker in text for marker in ("筛选", "排除st", "成交额超过")):
            return "market_scanner"
        if "分析" in text or _symbol_from_text(text):
            return "stock_research"
        if "系统诊断" in text:
            return "system_doctor"
        return "stock_research"

    def cancel(self, task_id: str) -> TaskRun:
        return self.repository.request_cancel(task_id)

    def retry(self, task_id: str) -> TaskRun:
        task, _ = self.repository.retry_task(
            task_id, request_id=new_request_id()
        )
        return self._execute(task, str(task.input_payload.get("user_input") or ""))

    def export_conversation(self, conversation_id: str, destination: Path) -> Path:
        messages = self.repository.messages(conversation_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Hermes OPC 会话导出", "", "仅包含用户输入和结构化系统状态。", ""]
        for message in messages:
            lines.extend(
                [
                    f"## {message.role} · {message.created_at.isoformat()}",
                    "",
                    message.content,
                    "",
                ]
            )
        destination.write_text("\n".join(lines), encoding="utf-8")
        return destination


__all__ = ["TaskCenterService"]
