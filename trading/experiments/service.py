from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from config.settings import settings
from config.version import PROJECT_VERSION
from trading.experiments.forward_returns import ForwardReturnLabelService
from trading.experiments.hashing import (
    file_sha256,
    read_git_head,
    stable_hash,
    stable_id,
)
from trading.experiments.historical_replay import HistoricalReplayService
from trading.experiments.metrics import EvaluationMetricService
from trading.experiments.models import (
    ExperimentRiskFlag,
    ExperimentStatus,
    LabelStatus,
    SignalType,
)
from trading.experiments.portfolio import PortfolioBacktestService
from trading.experiments.repository import ExperimentRepository
from trading.experiments.schemas import (
    CreateExperimentRequest,
    CreateExperimentResponse,
    ExperimentDefinition,
    ExperimentObservation,
    ExperimentReportResponse,
    ExperimentRun,
    ForwardReturnUpdateRequest,
    ForwardReturnUpdateResponse,
    HistoricalReplayRequest,
    HistoricalReplayResponse,
    MetricsResponse,
    PortfolioBacktestRequest,
    PortfolioBacktestResult,
    RunExperimentRequest,
    RunExperimentResponse,
    build_definition,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _risk_flags(values: list[Any]) -> list[ExperimentRiskFlag]:
    result: list[ExperimentRiskFlag] = []
    for value in values:
        raw = value.value if hasattr(value, "value") else str(value)
        try:
            result.append(ExperimentRiskFlag(raw))
        except ValueError:
            continue
    return list(dict.fromkeys(result))


class ExperimentEvaluationService:
    """Research-only orchestration for experiments, replay, labels, and metrics."""

    def __init__(
        self,
        repository: ExperimentRepository | None = None,
    ) -> None:
        self.repository = repository or ExperimentRepository()
        self.labeler = ForwardReturnLabelService()
        self.replay_service = HistoricalReplayService()
        self.metric_service = EvaluationMetricService()
        self.portfolio_service = PortfolioBacktestService()

    def create_experiment(
        self,
        request: CreateExperimentRequest,
    ) -> CreateExperimentResponse:
        definition = build_definition(
            request,
            created_at=datetime.now().astimezone(),
        )
        existing = self.repository.find_definition_by_hash(
            definition.config_hash
        )
        if existing is not None:
            definition = existing
        persisted = False
        if request.persist and existing is None:
            persisted = self.repository.save_definition(definition)
        return CreateExperimentResponse(
            experiment=definition,
            persisted=persisted,
            existing=existing is not None,
            point_in_time_status="REGISTERED",
            sample_count=0,
            data_quality_summary={
                "stable_config_hash": True,
                "production_weight_update": False,
                "trade_execution_enabled": False,
            },
            risk_flags=[
                ExperimentRiskFlag.RESEARCH_ONLY,
                ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
            ],
        )

    def get_experiment(self, experiment_id: str) -> ExperimentDefinition:
        value = self.repository.get_definition(experiment_id)
        if value is None:
            raise KeyError(f"experiment not found: {experiment_id}")
        return value

    def get_run(self, run_id: str) -> ExperimentRun:
        value = self.repository.get_run(run_id)
        if value is None:
            raise KeyError(f"experiment run not found: {run_id}")
        return value

    def _snapshot_context(
        self,
        *,
        definition: ExperimentDefinition,
        source: Any,
    ) -> tuple[dict[str, Any], str, str, str]:
        summary = self.repository.dataset_summary()
        dataset_hash = stable_hash(summary)
        database_hash = file_sha256(Path(settings.opc_database_path))
        input_hash = stable_hash(
            {
                "config_hash": definition.config_hash,
                "dataset_snapshot_hash": dataset_hash,
                "source": source,
            }
        )
        return summary, database_hash, dataset_hash, input_hash

    def run_prospective(
        self,
        experiment_id: str,
        request: RunExperimentRequest,
    ) -> RunExperimentResponse:
        if request.source_scanner_run_id is None:
            raise ValueError(
                "prospective tracking requires source_scanner_run_id"
            )
        definition = self.get_experiment(experiment_id)
        context = self.repository.scanner_run_context(
            request.source_scanner_run_id
        )
        if context is None:
            raise KeyError(
                f"scanner run not found: {request.source_scanner_run_id}"
            )
        scanner_run, candidates = context
        started_at = datetime.now().astimezone()
        started = perf_counter()
        summary, database_hash, dataset_hash, input_hash = (
            self._snapshot_context(
                definition=definition,
                source={
                    "source_scanner_run_id": request.source_scanner_run_id,
                    "scanner_input_snapshot_hash": scanner_run.get(
                        "input_snapshot_hash"
                    ),
                },
            )
        )
        run_id = stable_id(
            "erun",
            {
                "experiment_id": experiment_id,
                "source_scanner_run_id": request.source_scanner_run_id,
                "input_snapshot_hash": input_hash,
            },
        )
        existing_run = self.repository.get_run(run_id)
        data_cutoff = datetime.fromisoformat(
            str(scanner_run.get("data_cutoff"))
        )
        stale = bool(scanner_run.get("stale", False))
        plan_hash = str(
            scanner_run.get("parsed_query", {}).get(
                "plan_hash",
                scanner_run.get("plan_hash", "0" * 64),
            )
        )
        observations = [
            self._scanner_observation(
                run_id=run_id,
                source_run_id=request.source_scanner_run_id,
                data_cutoff=data_cutoff,
                plan_hash=plan_hash,
                candidate=candidate,
                stale=stale,
            )
            for candidate in candidates
        ]
        generated_at = datetime.now().astimezone()
        labels = self.labeler.pending_labels(
            observations,
            generated_at=generated_at,
        )
        risk_flags = [
            ExperimentRiskFlag.INSUFFICIENT_FUTURE_DATA,
            ExperimentRiskFlag.RESEARCH_ONLY,
            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
        ]
        if stale:
            risk_flags.append(ExperimentRiskFlag.STALE_SIGNAL)
        quality = self.metric_service.data_quality(observations, labels)
        run = existing_run or ExperimentRun(
            run_id=run_id,
            experiment_id=experiment_id,
            run_type="PROSPECTIVE_SCANNER_REGISTRATION",
            started_at=started_at,
            completed_at=generated_at,
            code_version=PROJECT_VERSION,
            git_head=read_git_head(PROJECT_ROOT),
            database_snapshot_hash=database_hash,
            dataset_snapshot_hash=dataset_hash,
            input_snapshot_hash=input_hash,
            query_plan_hash=plan_hash,
            data_cutoff=data_cutoff,
            as_of_trade_date=summary["latest_calendar_trade_date"],
            available_history_start=summary["min_date"],
            available_history_end=summary["max_date"],
            eligible_signal_date_count=1,
            generated_signal_count=len(observations),
            labeled_signal_count=0,
            pending_label_count=len(labels),
            invalid_signal_count=sum(
                not item.point_in_time_valid for item in observations
            ),
            benchmark_available=False,
            elapsed_ms=(perf_counter() - started) * 1000,
            peak_memory_bytes=0,
            database_query_count=2,
            status=ExperimentStatus.INSUFFICIENT_DATA,
            risk_flags=risk_flags,
            data_quality_summary=quality,
            generated_at=generated_at,
        )
        persisted_observations = 0
        persisted_labels = 0
        if request.persist:
            self.repository.save_run(run)
            persisted_observations = self.repository.save_observations(
                observations
            )
            persisted_labels = self.repository.save_initial_labels(labels)
            self.repository.save_quality_audit(
                experiment_id=experiment_id,
                run_id=run_id,
                audit_type="PROSPECTIVE_REGISTRATION",
                status="INSUFFICIENT_FUTURE_DATA",
                sample_count=len(observations),
                details=quality,
                risk_flags=risk_flags,
                generated_at=generated_at,
            )
        return RunExperimentResponse(
            run=run,
            observations=observations,
            labels=labels,
            persisted_observation_count=persisted_observations,
            persisted_label_count=persisted_labels,
            point_in_time_status="VALID_STALE_PROSPECTIVE_SNAPSHOT",
            sample_count=len(observations),
            data_quality_summary=quality,
            risk_flags=risk_flags,
        )

    @staticmethod
    def _scanner_observation(
        *,
        run_id: str,
        source_run_id: str,
        data_cutoff: datetime,
        plan_hash: str,
        candidate: dict[str, Any],
        stale: bool,
    ) -> ExperimentObservation:
        symbol = str(candidate["symbol"])
        signal_id = stable_id(
            "sig",
            {
                "source_run_id": source_run_id,
                "symbol": symbol,
                "rank": candidate["rank"],
            },
        )
        raw_risks = [
            str(value)
            for value in candidate.get("risk_flags", [])
        ]
        risks = _risk_flags(raw_risks)
        if stale:
            risks.append(ExperimentRiskFlag.STALE_SIGNAL)
        if candidate.get("factor_coverage") in {"0/5", "1/5", "2/5"}:
            risks.append(ExperimentRiskFlag.LOW_FACTOR_COVERAGE)
        risks.extend(
            [
                ExperimentRiskFlag.RESEARCH_ONLY,
                ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
            ]
        )
        snapshot = {
            "source_run_id": source_run_id,
            "rank": candidate["rank"],
            "symbol": symbol,
            "scanner_score": candidate.get("scanner_score"),
            "technical_score": candidate.get("technical_score"),
            "shadow_score": candidate.get("shadow_composite_score"),
            "factor_coverage": candidate.get("factor_coverage"),
            "risk_flags": raw_risks,
        }
        return ExperimentObservation(
            observation_id=stable_id(
                "obs",
                {"run_id": run_id, "signal_id": signal_id, "symbol": symbol},
            ),
            run_id=run_id,
            signal_id=signal_id,
            signal_type=SignalType.SCANNER_ONLY,
            signal_trade_date=data_cutoff.date(),
            signal_generated_at=data_cutoff,
            data_cutoff=data_cutoff,
            actionable_from_trade_date=None,
            symbol=symbol,
            board=candidate.get("board"),
            industry=candidate.get("industry"),
            rank=int(candidate["rank"]),
            scanner_score=candidate.get("scanner_score"),
            technical_score=candidate.get("technical_score"),
            fundamental_score=None,
            formal_score=None,
            formal_action=None,
            shadow_score=candidate.get("shadow_composite_score"),
            composite_confidence=candidate.get("composite_confidence"),
            factor_coverage=str(candidate.get("factor_coverage", "0/5")),
            available_factors=[
                str(value)
                for value in candidate.get("available_factors", [])
            ],
            missing_factors=[
                str(value)
                for value in candidate.get("missing_factors", [])
            ],
            anomaly_types=[
                str(value)
                for value in candidate.get("anomaly_types", [])
            ],
            risk_flags=list(dict.fromkeys(risks)),
            veto_status="NOT_EVALUATED",
            query_plan_hash=plan_hash,
            signal_snapshot_hash=stable_hash(snapshot),
            source_run_id=source_run_id,
            stale_snapshot=stale,
            point_in_time_valid=True,
            is_suspended=(
                candidate.get("data_freshness") == "MISSING"
            ),
            data_complete=not bool(candidate.get("missing_fields")),
            created_at=data_cutoff,
        )

    def replay_historical(
        self,
        experiment_id: str,
        request: HistoricalReplayRequest,
    ) -> HistoricalReplayResponse:
        definition = self.get_experiment(experiment_id)
        summary, database_hash, dataset_hash, input_hash = (
            self._snapshot_context(
                definition=definition,
                source=request.model_dump(mode="json"),
            )
        )
        query_plan_hash = stable_hash(
            {
                "algorithm": "historical-price-volume-replay-v1",
                "fields": [
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                    "ma5",
                    "ma10",
                    "ma20",
                    "volume_ma5",
                    "volume_ma20",
                    "breakout_20d",
                ],
                "top_k": 20,
                "random_seed": request.random_seed,
            }
        )
        run_id = stable_id(
            "erun",
            {
                "experiment_id": experiment_id,
                "input_snapshot_hash": input_hash,
                "query_plan_hash": query_plan_hash,
            },
        )
        batch = self.replay_service.replay(
            request,
            run_id=run_id,
            query_plan_hash=query_plan_hash,
        )
        calculated_at = datetime.now().astimezone()
        labels = self.labeler.calculate(
            batch.observations,
            as_of=calculated_at,
        )
        quality = self.metric_service.data_quality(
            batch.observations,
            labels,
        )
        complete_count = sum(
            item.label_status == LabelStatus.COMPLETE for item in labels
        )
        pending_count = sum(
            item.label_status
            in {
                LabelStatus.PENDING,
                LabelStatus.INSUFFICIENT_FUTURE_DATA,
            }
            for item in labels
        )
        run = ExperimentRun(
            run_id=run_id,
            experiment_id=experiment_id,
            run_type="HISTORICAL_POINT_IN_TIME_REPLAY",
            started_at=calculated_at,
            completed_at=calculated_at,
            code_version=PROJECT_VERSION,
            git_head=read_git_head(PROJECT_ROOT),
            database_snapshot_hash=database_hash,
            dataset_snapshot_hash=dataset_hash,
            input_snapshot_hash=input_hash,
            query_plan_hash=query_plan_hash,
            data_cutoff=calculated_at,
            as_of_trade_date=summary["latest_calendar_trade_date"],
            available_history_start=summary["min_date"],
            available_history_end=summary["max_date"],
            eligible_signal_date_count=batch.signal_date_count,
            generated_signal_count=len(batch.observations),
            labeled_signal_count=complete_count,
            pending_label_count=pending_count,
            invalid_signal_count=sum(
                not item.point_in_time_valid for item in batch.observations
            ),
            benchmark_available=any(
                item.benchmark_return is not None for item in labels
            ),
            elapsed_ms=batch.elapsed_ms,
            peak_memory_bytes=batch.peak_memory_bytes,
            database_query_count=batch.database_query_count + 6,
            status=(
                ExperimentStatus.INSUFFICIENT_DATA
                if not batch.observations
                else ExperimentStatus.PARTIAL
            ),
            risk_flags=batch.risk_flags,
            data_quality_summary=quality,
            generated_at=calculated_at,
        )
        persisted_observations = 0
        persisted_labels = 0
        if request.persist:
            self.repository.save_run(run)
            persisted_observations = self.repository.save_observations(
                batch.observations
            )
            persisted_labels, _ = self.repository.upsert_labels(labels)
            metrics = self.metric_service.calculate(
                batch.observations,
                labels,
                minimum_sample_size=definition.minimum_sample_size,
                random_seed=definition.random_seed,
            )
            self.repository.save_metrics(
                run_id,
                metrics,
                generated_at=calculated_at,
            )
            self.repository.save_slices(
                run_id,
                self.metric_service.slices(
                    batch.observations,
                    labels,
                    minimum_sample_size=definition.minimum_sample_size,
                ),
                generated_at=calculated_at,
            )
            self.repository.save_quality_audit(
                experiment_id=experiment_id,
                run_id=run_id,
                audit_type="HISTORICAL_REPLAY",
                status="PARTIAL_REPLAY",
                sample_count=len(batch.observations),
                details=quality,
                risk_flags=batch.risk_flags,
                generated_at=calculated_at,
            )
        return HistoricalReplayResponse(
            run=run,
            observations=batch.observations,
            labels=labels,
            persisted_observation_count=persisted_observations,
            persisted_label_count=persisted_labels,
            historical_window=batch.window,
            signal_date_count=batch.signal_date_count,
            symbol_count=batch.symbol_count,
            query_count=run.database_query_count,
            point_in_time_status=(
                "PARTIAL_REPLAY"
                if batch.observations
                else "INSUFFICIENT_HISTORY"
            ),
            sample_count=len(batch.observations),
            data_quality_summary=quality,
            risk_flags=batch.risk_flags,
        )

    def update_forward_returns(
        self,
        request: ForwardReturnUpdateRequest,
    ) -> ForwardReturnUpdateResponse:
        run = (
            self.get_run(request.run_id)
            if request.run_id is not None
            else self.repository.latest_run(request.experiment_id or "")
        )
        if run is None:
            raise KeyError("experiment run not found")
        observations = self.repository.list_observations(run.run_id)
        labels = self.labeler.calculate(observations, as_of=request.as_of)
        changed = 0
        unchanged = 0
        if request.persist:
            changed, unchanged = self.repository.upsert_labels(labels)
        complete = sum(
            item.label_status == LabelStatus.COMPLETE for item in labels
        )
        insufficient = sum(
            item.label_status == LabelStatus.INSUFFICIENT_FUTURE_DATA
            for item in labels
        )
        if not observations:
            status = "SKIPPED_NO_OBSERVATIONS"
        elif request.persist and changed == 0:
            status = "SKIPPED_NO_MATURE_LABELS"
        elif complete == 0 and insufficient:
            status = "INSUFFICIENT_FUTURE_DATA"
        else:
            status = "UPDATED"
        quality = self.metric_service.data_quality(observations, labels)
        flags = [
            ExperimentRiskFlag.RESEARCH_ONLY,
            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
        ]
        if insufficient:
            flags.append(ExperimentRiskFlag.INSUFFICIENT_FUTURE_DATA)
        return ForwardReturnUpdateResponse(
            labels=labels,
            updated_count=changed,
            unchanged_count=unchanged,
            status=status,
            point_in_time_status="SIGNAL_SNAPSHOTS_UNCHANGED",
            sample_count=len(observations),
            data_quality_summary=quality,
            risk_flags=flags,
        )

    def metrics(self, run_id: str) -> MetricsResponse:
        run = self.get_run(run_id)
        definition = self.get_experiment(run.experiment_id)
        observations = self.repository.list_observations(run_id)
        labels = self.repository.list_labels(run_id)
        metrics = self.metric_service.calculate(
            observations,
            labels,
            minimum_sample_size=definition.minimum_sample_size,
            random_seed=definition.random_seed,
        )
        quality = self.metric_service.data_quality(observations, labels)
        slices = self.metric_service.slices(
            observations,
            labels,
            minimum_sample_size=definition.minimum_sample_size,
        )
        flags = list(
            dict.fromkeys(
                [
                    flag
                    for item in metrics
                    for flag in item.risk_flags
                ]
            )
        )
        return MetricsResponse(
            run_id=run_id,
            metrics=metrics,
            slices=slices,
            point_in_time_status="EXCLUDES_INVALID_AND_STALE_PRIMARY",
            sample_count=len(observations),
            data_quality_summary=quality,
            risk_flags=flags,
        )

    def observations(self, run_id: str) -> list[ExperimentObservation]:
        self.get_run(run_id)
        return self.repository.list_observations(run_id)

    def run_portfolio_backtest(
        self,
        request: PortfolioBacktestRequest,
    ) -> PortfolioBacktestResult:
        self.get_experiment(request.experiment_id)
        self.get_run(request.run_id)
        result = self.portfolio_service.run(
            request,
            observations=self.repository.list_observations(request.run_id),
            labels=self.repository.list_labels(request.run_id),
        )
        if request.persist:
            self.repository.save_backtest(
                result,
                generated_at=datetime.now().astimezone(),
            )
        return result

    def report(self, run_id: str) -> ExperimentReportResponse:
        run = self.get_run(run_id)
        definition = self.get_experiment(run.experiment_id)
        metrics = self.metrics(run_id)
        narrative = {
            "profitability_proven": False,
            "correlation_is_not_causation": True,
            "multiple_testing_risk": True,
            "formal_60_40": {
                "comparable": False,
                "reason": "fundamental coverage is insufficient",
                "missing_fundamental_is_zero": False,
            },
            "shadow_composite": {
                "shadow_mode": True,
                "formal_strategy_weight": 0,
            },
            "production_changes": {
                "weights_changed": False,
                "actions_changed": False,
                "veto_changed": False,
                "orders_created": False,
                "paper_trading_written": False,
            },
        }
        return ExperimentReportResponse(
            experiment=definition,
            run=run,
            metrics=metrics.metrics,
            narrative=narrative,
            point_in_time_status=metrics.point_in_time_status,
            sample_count=metrics.sample_count,
            data_quality_summary=metrics.data_quality_summary,
            risk_flags=metrics.risk_flags,
        )

    @staticmethod
    def history_extension_plan(window: Any) -> dict[str, Any]:
        minimum_window_available = (
            window.distinct_trade_dates >= window.required_history
        )
        risk_flags = [
            ExperimentRiskFlag.RESEARCH_ONLY.value,
            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY.value,
        ]
        if not minimum_window_available:
            risk_flags.insert(
                0,
                ExperimentRiskFlag.INSUFFICIENT_HISTORY.value,
            )
        return {
            "status": (
                "MINIMUM_WINDOW_AVAILABLE"
                if minimum_window_available
                else "INSUFFICIENT_HISTORY"
            ),
            "required_history_trading_days": window.required_history,
            "available_trade_dates": window.distinct_trade_dates,
            "missing_trade_dates": max(
                0,
                window.required_history - window.distinct_trade_dates,
            ),
            "automatic_backfill_started": False,
            "purpose": "planning only",
            "risk_flags": risk_flags,
        }

    @staticmethod
    def formal_shadow_boundary() -> dict[str, Any]:
        return {
            "formal_weights": {"TECHNICAL": 0.60, "FUNDAMENTAL": 0.40},
            "missing_fundamental_filled_with_zero": False,
            "formal_result_status": "INSUFFICIENT_COVERAGE",
            "shadow_mode": True,
            "shadow_formal_strategy_weight": 0,
            "formal_action_changed": False,
            "hard_veto_changed": False,
        }


__all__ = ["ExperimentEvaluationService"]
