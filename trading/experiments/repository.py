from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import duckdb
import pandas as pd

from database.db import get_connection, initialize_database
from trading.experiments.hashing import canonical_json, stable_id
from trading.experiments.schemas import (
    ExperimentDefinition,
    ExperimentObservation,
    ExperimentRun,
    ForwardReturnLabel,
    PortfolioBacktestResult,
    SignalMetricSet,
)


def _load_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class ExperimentRepository:
    """Persistence boundary for immutable signals and separate mutable labels."""

    def __init__(self) -> None:
        initialize_database()

    def find_definition_by_hash(
        self,
        config_hash: str,
    ) -> ExperimentDefinition | None:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                SELECT * FROM experiment_definitions WHERE config_hash = ?
                """,
                [config_hash],
            )
            row = cursor.fetchone()
            columns = [item[0] for item in cursor.description]
        if row is None:
            return None
        payload = dict(zip(columns, row, strict=True))
        payload["holding_periods"] = _load_json(
            payload.pop("holding_periods_json")
        )
        payload["universe_definition"] = _load_json(payload["universe_definition"])
        payload["config_json"] = _load_json(payload["config_json"])
        if payload["cost_model"] is not None:
            payload["cost_model"] = _load_json(payload["cost_model"])
        return ExperimentDefinition.model_validate(payload)

    def save_definition(self, definition: ExperimentDefinition) -> bool:
        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT experiment_id
                FROM experiment_definitions
                WHERE config_hash = ?
                """,
                [definition.config_hash],
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """
                INSERT INTO experiment_definitions (
                    experiment_id, experiment_name, experiment_type,
                    description, hypothesis, universe_definition,
                    query_plan_id, scanner_version, orchestration_version,
                    formal_strategy_version, shadow_strategy_version,
                    signal_frequency, signal_time_policy, entry_policy,
                    exit_policy, holding_periods_json, top_k,
                    weighting_method, benchmark_definition, cost_model,
                    missing_data_policy, stale_data_policy,
                    minimum_sample_size, start_trade_date, end_trade_date,
                    random_seed, config_json, config_hash, version,
                    created_at, research_only, production_weight_update,
                    trade_execution_enabled, experimental_only, status
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    definition.experiment_id,
                    definition.experiment_name,
                    definition.experiment_type.value,
                    definition.description,
                    definition.hypothesis,
                    canonical_json(definition.universe_definition),
                    definition.query_plan_id,
                    definition.scanner_version,
                    definition.orchestration_version,
                    definition.formal_strategy_version,
                    definition.shadow_strategy_version,
                    definition.signal_frequency,
                    definition.signal_time_policy,
                    definition.entry_policy,
                    definition.exit_policy,
                    canonical_json(definition.holding_periods),
                    definition.top_k,
                    definition.weighting_method.value,
                    definition.benchmark_definition,
                    (
                        None
                        if definition.cost_model is None
                        else canonical_json(definition.cost_model)
                    ),
                    definition.missing_data_policy,
                    definition.stale_data_policy,
                    definition.minimum_sample_size,
                    definition.start_trade_date,
                    definition.end_trade_date,
                    definition.random_seed,
                    canonical_json(definition.config_json),
                    definition.config_hash,
                    definition.version,
                    definition.created_at,
                    True,
                    False,
                    False,
                    definition.experimental_only,
                    definition.status.value,
                ],
            )
        return True

    def get_definition(self, experiment_id: str) -> ExperimentDefinition | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT config_hash
                FROM experiment_definitions
                WHERE experiment_id = ?
                """,
                [experiment_id],
            ).fetchone()
        return None if row is None else self.find_definition_by_hash(row[0])

    def save_run(self, run: ExperimentRun) -> bool:
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT run_id FROM experiment_runs WHERE run_id = ?",
                [run.run_id],
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """
                INSERT INTO experiment_runs (
                    run_id, experiment_id, run_type, started_at, completed_at,
                    code_version, git_head, migration_version,
                    database_snapshot_hash, dataset_snapshot_hash,
                    input_snapshot_hash, query_plan_hash, data_cutoff,
                    as_of_trade_date, available_history_start,
                    available_history_end, eligible_signal_date_count,
                    generated_signal_count, labeled_signal_count,
                    pending_label_count, invalid_signal_count,
                    benchmark_available, elapsed_ms, peak_memory_bytes,
                    database_query_count, network_request_count,
                    model_call_count, status, risk_flags_json,
                    data_quality_summary_json, sanitized_error, payload_json,
                    generated_at, research_only, profitability_proven
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    run.run_id,
                    run.experiment_id,
                    run.run_type,
                    run.started_at,
                    run.completed_at,
                    run.code_version,
                    run.git_head,
                    run.migration_version,
                    run.database_snapshot_hash,
                    run.dataset_snapshot_hash,
                    run.input_snapshot_hash,
                    run.query_plan_hash,
                    run.data_cutoff,
                    run.as_of_trade_date,
                    run.available_history_start,
                    run.available_history_end,
                    run.eligible_signal_date_count,
                    run.generated_signal_count,
                    run.labeled_signal_count,
                    run.pending_label_count,
                    run.invalid_signal_count,
                    run.benchmark_available,
                    run.elapsed_ms,
                    run.peak_memory_bytes,
                    run.database_query_count,
                    0,
                    0,
                    run.status.value,
                    canonical_json(run.risk_flags),
                    canonical_json(run.data_quality_summary),
                    run.sanitized_error,
                    run.model_dump_json(),
                    run.generated_at,
                    True,
                    False,
                ],
            )
        return True

    def get_run(self, run_id: str) -> ExperimentRun | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM experiment_runs WHERE run_id = ?",
                [run_id],
            ).fetchone()
        return None if row is None else ExperimentRun.model_validate(_load_json(row[0]))

    def latest_run(self, experiment_id: str) -> ExperimentRun | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM experiment_runs
                WHERE experiment_id = ?
                ORDER BY generated_at DESC, run_id DESC
                LIMIT 1
                """,
                [experiment_id],
            ).fetchone()
        return None if row is None else ExperimentRun.model_validate(_load_json(row[0]))

    def latest_experiment_id(self) -> str | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT experiment_id
                FROM experiment_runs
                ORDER BY generated_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
        return None if row is None else str(row[0])

    def save_observations(
        self,
        observations: list[ExperimentObservation],
    ) -> int:
        if not observations:
            return 0
        with get_connection() as connection:
            existing = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT observation_id
                    FROM experiment_observations
                    WHERE observation_id IN (
                        SELECT UNNEST(?::VARCHAR[])
                    )
                    """,
                    [[item.observation_id for item in observations]],
                ).fetchall()
            }
            new_items = [
                item
                for item in observations
                if item.observation_id not in existing
            ]
            if not new_items:
                return 0
            frame = pd.DataFrame(
                [
                    {
                        "observation_id": item.observation_id,
                        "run_id": item.run_id,
                        "signal_id": item.signal_id,
                        "signal_type": item.signal_type.value,
                        "signal_trade_date": item.signal_trade_date,
                        "signal_generated_at": item.signal_generated_at,
                        "data_cutoff": item.data_cutoff,
                        "actionable_from_trade_date": (
                            item.actionable_from_trade_date
                        ),
                        "symbol": item.symbol,
                        "rank": item.rank,
                        "scanner_score": item.scanner_score,
                        "technical_score": item.technical_score,
                        "fundamental_score": item.fundamental_score,
                        "formal_score": item.formal_score,
                        "formal_action": item.formal_action,
                        "shadow_score": item.shadow_score,
                        "composite_confidence": item.composite_confidence,
                        "factor_coverage": item.factor_coverage,
                        "available_factors_json": canonical_json(
                            item.available_factors
                        ),
                        "missing_factors_json": canonical_json(
                            item.missing_factors
                        ),
                        "anomaly_types_json": canonical_json(
                            item.anomaly_types
                        ),
                        "risk_flags_json": canonical_json(item.risk_flags),
                        "veto_status": item.veto_status,
                        "query_plan_hash": item.query_plan_hash,
                        "signal_snapshot_hash": item.signal_snapshot_hash,
                        "source_run_id": item.source_run_id,
                        "stale_snapshot": item.stale_snapshot,
                        "point_in_time_valid": item.point_in_time_valid,
                        "payload_json": item.model_dump_json(),
                        "created_at": item.created_at,
                    }
                    for item in new_items
                ]
            )
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.register("_experiment_observation_batch", frame)
                connection.execute(
                    """
                    INSERT INTO experiment_observations (
                        observation_id, run_id, signal_id, signal_type,
                        signal_trade_date, signal_generated_at,
                        data_cutoff, actionable_from_trade_date, symbol,
                        rank, scanner_score, technical_score,
                        fundamental_score, formal_score, formal_action,
                        shadow_score, composite_confidence,
                        factor_coverage, available_factors_json,
                        missing_factors_json, anomaly_types_json,
                        risk_flags_json, veto_status, query_plan_hash,
                        signal_snapshot_hash, source_run_id,
                        stale_snapshot, point_in_time_valid,
                        payload_json, created_at
                    )
                    SELECT
                        observation_id, run_id, signal_id, signal_type,
                        signal_trade_date, signal_generated_at,
                        data_cutoff, actionable_from_trade_date, symbol,
                        rank, scanner_score, technical_score,
                        fundamental_score, formal_score, formal_action,
                        shadow_score, composite_confidence,
                        factor_coverage, available_factors_json,
                        missing_factors_json, anomaly_types_json,
                        risk_flags_json, veto_status, query_plan_hash,
                        signal_snapshot_hash, source_run_id,
                        stale_snapshot, point_in_time_valid,
                        payload_json, created_at
                    FROM _experiment_observation_batch
                    ON CONFLICT DO NOTHING
                    """
                )
                connection.unregister("_experiment_observation_batch")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return len(new_items)

    def list_observations(self, run_id: str) -> list[ExperimentObservation]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM experiment_observations
                WHERE run_id = ?
                ORDER BY signal_trade_date, signal_type, rank, symbol
                """,
                [run_id],
            ).fetchall()
        return [
            ExperimentObservation.model_validate(_load_json(row[0]))
            for row in rows
        ]

    def save_initial_labels(
        self,
        labels: list[ForwardReturnLabel],
    ) -> int:
        if not labels:
            return 0
        with get_connection() as connection:
            existing = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT label_id FROM forward_return_labels
                    WHERE label_id IN (SELECT UNNEST(?::VARCHAR[]))
                    """,
                    [[item.label_id for item in labels]],
                ).fetchall()
            }
            new_labels = [
                item for item in labels if item.label_id not in existing
            ]
            if not new_labels:
                return 0
            columns = [
                "label_id",
                "observation_id",
                "symbol",
                "signal_trade_date",
                "entry_trade_date",
                "exit_trade_date",
                "entry_price",
                "exit_price",
                "entry_price_type",
                "exit_price_type",
                "gross_return",
                "benchmark_return",
                "excess_return",
                "maximum_favorable_excursion",
                "maximum_adverse_excursion",
                "was_suspended_on_entry",
                "was_suspended_during_holding",
                "was_price_limit_locked",
                "missing_price_reason",
                "return_basis",
                "horizon_trading_days",
                "label_status",
                "risk_flags_json",
                "calculated_at",
                "label_version",
                "payload_json",
                "generated_at",
            ]
            frame = pd.DataFrame(
                [self._label_values(label) for label in new_labels],
                columns=columns,
            )
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.register("_forward_label_batch", frame)
                connection.execute(
                    """
                    INSERT INTO forward_return_labels
                    SELECT * FROM _forward_label_batch
                    ON CONFLICT DO NOTHING
                    """
                )
                connection.unregister("_forward_label_batch")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return len(new_labels)

    @staticmethod
    def _label_insert_sql() -> str:
        return """
            INSERT INTO forward_return_labels (
                label_id, observation_id, symbol, signal_trade_date,
                entry_trade_date, exit_trade_date, entry_price, exit_price,
                entry_price_type, exit_price_type, gross_return,
                benchmark_return, excess_return,
                maximum_favorable_excursion, maximum_adverse_excursion,
                was_suspended_on_entry, was_suspended_during_holding,
                was_price_limit_locked, missing_price_reason, return_basis,
                horizon_trading_days, label_status, risk_flags_json,
                calculated_at, label_version, payload_json, generated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?
            )
            """

    @staticmethod
    def _label_values(label: ForwardReturnLabel) -> list[Any]:
        return [
                label.label_id,
                label.observation_id,
                label.symbol,
                label.signal_trade_date,
                label.entry_trade_date,
                label.exit_trade_date,
                label.entry_price,
                label.exit_price,
                label.entry_price_type,
                label.exit_price_type,
                label.gross_return,
                label.benchmark_return,
                label.excess_return,
                label.maximum_favorable_excursion,
                label.maximum_adverse_excursion,
                label.was_suspended_on_entry,
                label.was_suspended_during_holding,
                label.was_price_limit_locked,
                label.missing_price_reason,
                label.return_basis,
                label.horizon_trading_days,
                label.label_status.value,
                canonical_json(label.risk_flags),
                label.calculated_at,
                label.label_version,
                label.model_dump_json(),
                label.generated_at,
            ]

    @classmethod
    def _insert_label(
        cls,
        connection: duckdb.DuckDBPyConnection,
        label: ForwardReturnLabel,
    ) -> None:
        connection.execute(cls._label_insert_sql(), cls._label_values(label))

    def upsert_labels(
        self,
        labels: list[ForwardReturnLabel],
    ) -> tuple[int, int]:
        if not labels:
            return 0, 0
        with get_connection() as connection:
            existing_rows = connection.execute(
                """
                SELECT label_id, payload_json
                FROM forward_return_labels
                WHERE label_id IN (SELECT UNNEST(?::VARCHAR[]))
                """,
                [[item.label_id for item in labels]],
            ).fetchall()
            existing = {
                row[0]: ForwardReturnLabel.model_validate(_load_json(row[1]))
                for row in existing_rows
            }
            new_labels: list[ForwardReturnLabel] = []
            changed_labels: list[ForwardReturnLabel] = []
            unchanged = 0
            for label in labels:
                current = existing.get(label.label_id)
                if current is None:
                    new_labels.append(label)
                    continue
                current_semantic = current.model_dump(
                    exclude={"calculated_at", "generated_at"}
                )
                label_semantic = label.model_dump(
                    exclude={"calculated_at", "generated_at"}
                )
                if current_semantic == label_semantic:
                    unchanged += 1
                else:
                    changed_labels.append(label)
            columns = [
                "label_id",
                "observation_id",
                "symbol",
                "signal_trade_date",
                "entry_trade_date",
                "exit_trade_date",
                "entry_price",
                "exit_price",
                "entry_price_type",
                "exit_price_type",
                "gross_return",
                "benchmark_return",
                "excess_return",
                "maximum_favorable_excursion",
                "maximum_adverse_excursion",
                "was_suspended_on_entry",
                "was_suspended_during_holding",
                "was_price_limit_locked",
                "missing_price_reason",
                "return_basis",
                "horizon_trading_days",
                "label_status",
                "risk_flags_json",
                "calculated_at",
                "label_version",
                "payload_json",
                "generated_at",
            ]
            connection.execute("BEGIN TRANSACTION")
            try:
                if new_labels:
                    new_frame = pd.DataFrame(
                        [
                            self._label_values(label)
                            for label in new_labels
                        ],
                        columns=columns,
                    )
                    connection.register("_new_forward_labels", new_frame)
                    connection.execute(
                        """
                        INSERT INTO forward_return_labels
                        SELECT * FROM _new_forward_labels
                        ON CONFLICT DO NOTHING
                        """,
                    )
                    connection.unregister("_new_forward_labels")
                if changed_labels:
                    changed_frame = pd.DataFrame(
                        [
                            self._label_values(label)
                            for label in changed_labels
                        ],
                        columns=columns,
                    )
                    connection.register(
                        "_changed_forward_labels",
                        changed_frame,
                    )
                    connection.execute(
                        """
                        UPDATE forward_return_labels AS target SET
                            entry_trade_date = source.entry_trade_date,
                            exit_trade_date = source.exit_trade_date,
                            entry_price = source.entry_price,
                            exit_price = source.exit_price,
                            gross_return = source.gross_return,
                            benchmark_return = source.benchmark_return,
                            excess_return = source.excess_return,
                            maximum_favorable_excursion =
                                source.maximum_favorable_excursion,
                            maximum_adverse_excursion =
                                source.maximum_adverse_excursion,
                            was_suspended_on_entry =
                                source.was_suspended_on_entry,
                            was_suspended_during_holding =
                                source.was_suspended_during_holding,
                            was_price_limit_locked =
                                source.was_price_limit_locked,
                            missing_price_reason = source.missing_price_reason,
                            label_status = source.label_status,
                            risk_flags_json = source.risk_flags_json,
                            calculated_at = source.calculated_at,
                            payload_json = source.payload_json
                        FROM _changed_forward_labels AS source
                        WHERE target.label_id = source.label_id
                        """,
                    )
                    connection.unregister("_changed_forward_labels")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return len(new_labels) + len(changed_labels), unchanged

    def list_labels(self, run_id: str) -> list[ForwardReturnLabel]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT label.payload_json
                FROM forward_return_labels AS label
                JOIN experiment_observations AS observation
                  USING (observation_id)
                WHERE observation.run_id = ?
                ORDER BY observation.signal_trade_date,
                         observation.signal_type,
                         observation.rank,
                         label.horizon_trading_days
                """,
                [run_id],
            ).fetchall()
        return [
            ForwardReturnLabel.model_validate(_load_json(row[0])) for row in rows
        ]

    def scanner_run_context(
        self,
        run_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        with get_connection() as connection:
            run = connection.execute(
                "SELECT payload_json FROM scanner_runs WHERE run_id = ?",
                [run_id],
            ).fetchone()
            rows = connection.execute(
                """
                SELECT payload_json
                FROM scanner_candidates
                WHERE run_id = ?
                ORDER BY rank
                """,
                [run_id],
            ).fetchall()
        if run is None:
            return None
        return _load_json(run[0]), [_load_json(row[0]) for row in rows]

    def dataset_summary(self) -> dict[str, Any]:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT
                    count(*) AS row_count,
                    count(DISTINCT symbol) AS symbol_count,
                    count(DISTINCT trade_date) AS trade_date_count,
                    min(trade_date) AS min_date,
                    max(trade_date) AS max_date,
                    count(*) FILTER (WHERE adjustment_type <> 'RAW') AS non_raw,
                    count(*) FILTER (
                        WHERE verification_status = 'CONFLICT'
                    ) AS conflicts
                FROM canonical_historical_bars
                """
            ).fetchone()
            latest = connection.execute(
                """
                SELECT max(calendar_date)
                FROM trading_calendar_days
                WHERE is_trading_day
                """
            ).fetchone()[0]
        return {
            "row_count": row[0],
            "symbol_count": row[1],
            "trade_date_count": row[2],
            "min_date": row[3],
            "max_date": row[4],
            "non_raw_count": row[5],
            "conflict_count": row[6],
            "latest_calendar_trade_date": latest,
        }

    def save_metrics(
        self,
        run_id: str,
        metrics: list[SignalMetricSet],
        *,
        generated_at: datetime,
    ) -> int:
        inserted = 0
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                for item in metrics:
                    for name, value in item.model_dump(mode="json").items():
                        if name in {
                            "group_name",
                            "horizon_trading_days",
                            "risk_flags",
                        }:
                            continue
                        if not isinstance(value, (int, float)) and value is not None:
                            continue
                        identity = {
                            "run_id": run_id,
                            "scope": "SIGNAL",
                            "group": item.group_name,
                            "horizon": item.horizon_trading_days,
                            "metric": name,
                        }
                        existing = connection.execute(
                            """
                            SELECT metric_id FROM evaluation_metrics
                            WHERE run_id = ? AND scope = 'SIGNAL'
                              AND group_name = ?
                              AND horizon_trading_days = ?
                              AND metric_name = ?
                            """,
                            [
                                run_id,
                                item.group_name,
                                item.horizon_trading_days,
                                name,
                            ],
                        ).fetchone()
                        values = [
                            stable_id("met", identity),
                            run_id,
                            item.group_name,
                            item.horizon_trading_days,
                            name,
                            value,
                            item.sample_count,
                            (
                                "AVAILABLE"
                                if value is not None
                                else "INSUFFICIENT_DATA"
                            ),
                            (
                                canonical_json(item.confidence_interval)
                                if item.confidence_interval is not None
                                else None
                            ),
                            canonical_json(item.risk_flags),
                            generated_at,
                        ]
                        if existing is None:
                            connection.execute(
                                """
                                INSERT INTO evaluation_metrics (
                                    metric_id, run_id, scope, group_name,
                                    horizon_trading_days, metric_name,
                                    metric_value, sample_count, status,
                                    confidence_interval_json,
                                    risk_flags_json, generated_at
                                )
                                VALUES (
                                    ?, ?, 'SIGNAL', ?, ?, ?, ?, ?, ?, ?, ?, ?
                                )
                                """,
                                values,
                            )
                        else:
                            connection.execute(
                                """
                                UPDATE evaluation_metrics SET
                                    metric_value = ?, sample_count = ?,
                                    status = ?, confidence_interval_json = ?,
                                    risk_flags_json = ?, generated_at = ?
                                WHERE metric_id = ?
                                """,
                                [
                                    values[5],
                                    values[6],
                                    values[7],
                                    values[8],
                                    values[9],
                                    values[10],
                                    existing[0],
                                ],
                            )
                        inserted += existing is None
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return inserted

    def save_slices(
        self,
        run_id: str,
        slices: list[dict[str, Any]],
        *,
        generated_at: datetime,
    ) -> int:
        inserted = 0
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                for item in slices:
                    identity = {
                        "run_id": run_id,
                        "dimension": item["dimension"],
                        "slice_value": item["slice_value"],
                        "horizon": item["horizon_trading_days"],
                    }
                    existing = connection.execute(
                        """
                        SELECT slice_id FROM evaluation_slices
                        WHERE run_id = ? AND dimension = ?
                          AND slice_value = ?
                          AND horizon_trading_days = ?
                        """,
                        [
                            run_id,
                            item["dimension"],
                            item["slice_value"],
                            item["horizon_trading_days"],
                        ],
                    ).fetchone()
                    values = [
                        stable_id("slc", identity),
                        run_id,
                        item["dimension"],
                        item["slice_value"],
                        item["horizon_trading_days"],
                        item["sample_count"],
                        item["minimum_sample_size"],
                        item["conclusion_available"],
                        canonical_json(item["metrics"]),
                        canonical_json(item["risk_flags"]),
                        generated_at,
                    ]
                    if existing is None:
                        connection.execute(
                            """
                            INSERT INTO evaluation_slices (
                                slice_id, run_id, dimension, slice_value,
                                horizon_trading_days, sample_count,
                                minimum_sample_size, conclusion_available,
                                metrics_json, risk_flags_json, generated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            values,
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE evaluation_slices SET
                                sample_count = ?, minimum_sample_size = ?,
                                conclusion_available = ?, metrics_json = ?,
                                risk_flags_json = ?, generated_at = ?
                            WHERE slice_id = ?
                            """,
                            [
                                values[5],
                                values[6],
                                values[7],
                                values[8],
                                values[9],
                                values[10],
                                existing[0],
                            ],
                        )
                    inserted += existing is None
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return inserted

    def save_backtest(
        self,
        result: PortfolioBacktestResult,
        *,
        generated_at: datetime,
    ) -> bool:
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT backtest_id FROM backtest_runs WHERE backtest_id = ?",
                [result.backtest_id],
            ).fetchone()
            if existing is not None:
                return False
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO backtest_runs (
                        backtest_id, run_id, experiment_id,
                        portfolio_method, rebalance_method, weighting_method,
                        horizon_trading_days, top_k, max_position_weight,
                        cost_model_json, gross_return, net_return,
                        benchmark_return, turnover, active_days, status,
                        risk_flags_json, metrics_json, generated_at,
                        research_only, trade_execution_enabled,
                        paper_trading_written
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, TRUE, FALSE, FALSE
                    )
                    """,
                    [
                        result.backtest_id,
                        result.run_id,
                        result.experiment_id,
                        "RESEARCH_ONLY",
                        result.rebalance_method.value,
                        result.weighting_method.value,
                        result.horizon_trading_days,
                        result.top_k,
                        result.max_position_weight,
                        None,
                        result.gross_return,
                        result.net_return,
                        result.benchmark_return,
                        result.turnover,
                        result.active_days,
                        (
                            "COMPLETED"
                            if result.gross_return is not None
                            else "INSUFFICIENT_DATA"
                        ),
                        canonical_json(result.risk_flags),
                        result.model_dump_json(exclude={"positions"}),
                        generated_at,
                    ],
                )
                for position in result.positions:
                    connection.execute(
                        """
                        INSERT INTO backtest_positions (
                            backtest_id, cohort_id, signal_trade_date,
                            symbol, weight, entry_trade_date, exit_trade_date,
                            gross_return, net_return, tradable,
                            risk_flags_json, generated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            result.backtest_id,
                            position["cohort_id"],
                            position["signal_trade_date"],
                            position["symbol"],
                            position["weight"],
                            position.get("entry_trade_date"),
                            position.get("exit_trade_date"),
                            position.get("gross_return"),
                            position.get("net_return"),
                            position.get("tradable", False),
                            canonical_json(position.get("risk_flags", [])),
                            generated_at,
                        ],
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return True

    def save_quality_audit(
        self,
        *,
        experiment_id: str,
        run_id: str | None,
        audit_type: str,
        status: str,
        sample_count: int,
        details: dict[str, Any],
        risk_flags: list[Any],
        generated_at: datetime,
    ) -> bool:
        identity = {
            "experiment_id": experiment_id,
            "run_id": run_id,
            "audit_type": audit_type,
            "details": details,
        }
        audit_id = stable_id("eqa", identity)
        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT audit_id FROM experiment_data_quality_audits
                WHERE audit_id = ?
                """,
                [audit_id],
            ).fetchone()
            connection.execute(
                """
                INSERT INTO experiment_data_quality_audits (
                    audit_id, experiment_id, run_id, audit_type, status,
                    sample_count, details_json, risk_flags_json, generated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    audit_id,
                    experiment_id,
                    run_id,
                    audit_type,
                    status,
                    sample_count,
                    canonical_json(details),
                    canonical_json(risk_flags),
                    generated_at,
                ],
            )
        return existing is None


__all__ = ["ExperimentRepository"]
