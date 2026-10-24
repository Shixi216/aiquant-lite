from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from database.db import get_connection, initialize_database
from data_hub.repositories import FactorOutputRepository
from data_hub.schemas.unified import FactorOutput, FactorType
from trading.research.capital_flow.models import MarketBar
from trading.decision_support.formal_veto import (
    FormalRiskEvent,
    FormalRiskInput,
)
from trading.research.orchestration.schemas import (
    EvaluationRequest,
    FactorBundle,
    ShadowComposite,
)


def _load(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _margin_hash(
    snapshot_id: str, symbol: str, score: float, data_cutoff: datetime
) -> str:
    payload = (
        f"margin-snapshot-v1|{snapshot_id}|{symbol}|{score}|"
        f"{data_cutoff.isoformat() if data_cutoff is not None else ''}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class BulkFactorData:
    symbols: list[str]
    bars_by_symbol: dict[str, list[MarketBar]]
    factor_outputs: dict[str, dict[FactorType, FactorOutput]]
    sentiment_snapshots: dict[str, dict[str, Any]]
    policy_news_snapshots: dict[str, dict[str, Any]]
    margin_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    database_connection_count: int = 1


class OrchestrationRepository:
    """Bulk point-in-time reads and append-only orchestration audit writes."""

    def __init__(
        self,
        factor_repository: FactorOutputRepository | None = None,
    ) -> None:
        initialize_database()
        self.factor_repository = factor_repository or FactorOutputRepository()

    @staticmethod
    def _symbol_filter(
        symbols: list[str] | None,
        *,
        alias: str = "",
    ) -> tuple[str, list[str]]:
        if not symbols:
            return "", []
        prefix = f"{alias}." if alias else ""
        placeholders = ",".join("?" for _ in symbols)
        return f"AND {prefix}symbol IN ({placeholders})", symbols

    def load_bulk(
        self,
        *,
        data_cutoff: datetime,
        symbols: list[str] | None = None,
    ) -> BulkFactorData:
        requested = list(dict.fromkeys(symbols or []))
        with get_connection() as connection:
            if requested:
                selected = requested
            else:
                version = connection.execute(
                    """
                    SELECT universe_version
                    FROM stock_universe
                    WHERE data_available_time <= ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    [data_cutoff],
                ).fetchone()
                if version is None:
                    selected = []
                else:
                    selected = [
                        row[0]
                        for row in connection.execute(
                            """
                            SELECT symbol
                            FROM stock_universe
                            WHERE universe_version = ?
                              AND listing_status = 'ACTIVE'
                            ORDER BY symbol
                            """,
                            [version[0]],
                        ).fetchall()
                    ]

            symbol_sql, symbol_parameters = self._symbol_filter(selected)
            bar_rows = connection.execute(
                f"""
                SELECT bar_id, symbol, event_time, data_cutoff,
                       open, high, low, close, volume, amount,
                       source_record_ids_json
                FROM canonical_historical_bars
                WHERE adjustment_type = 'RAW'
                  AND event_time <= ?
                  AND data_available_time <= ?
                  AND data_cutoff <= ?
                  AND verification_status <> 'CONFLICT'
                  {symbol_sql}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY event_time DESC, bar_id DESC
                ) <= 61
                ORDER BY symbol, event_time, bar_id
                """,
                [data_cutoff, data_cutoff, data_cutoff, *symbol_parameters],
            ).fetchall()

            factor_sql, factor_parameters = self._symbol_filter(selected)
            factor_rows = connection.execute(
                f"""
                SELECT factor_id, symbol, factor_type, score, confidence,
                       data_cutoff, generated_at, evidence_ids_json,
                       risk_flags_json, model_call_ids_json,
                       algorithm_version, input_snapshot_hash,
                       shadow_mode, metadata_json
                FROM factor_outputs
                WHERE data_cutoff <= ?
                  AND factor_type <> 'SHADOW_COMPOSITE'
                  {factor_sql}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol, factor_type
                    ORDER BY data_cutoff DESC, generated_at DESC, factor_id
                ) = 1
                """,
                [data_cutoff, *factor_parameters],
            ).fetchall()

            sentiment_sql, sentiment_parameters = self._symbol_filter(selected)
            sentiment_rows = connection.execute(
                f"""
                SELECT snapshot_id, symbol, data_cutoff, score, confidence,
                       evidence_ids_json, risk_flags_json,
                       input_snapshot_hash, algorithm_version, generated_at,
                       shadow_mode
                FROM sentiment_symbol_snapshots
                WHERE data_cutoff <= ?
                  {sentiment_sql}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY data_cutoff DESC, generated_at DESC, snapshot_id
                ) = 1
                """,
                [data_cutoff, *sentiment_parameters],
            ).fetchall()

            policy_sql, policy_parameters = self._symbol_filter(selected)
            policy_rows = connection.execute(
                f"""
                SELECT snapshot_id, symbol, data_cutoff,
                       weighted_policy_score, confidence,
                       evidence_ids_json, risk_flags_json,
                       input_snapshot_hash, algorithm_version, generated_at,
                       shadow_mode, shared_sentiment_event_ids_json
                FROM policy_news_symbol_snapshots
                WHERE data_cutoff <= ?
                  {policy_sql}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY data_cutoff DESC, generated_at DESC, snapshot_id
                ) = 1
                """,
                [data_cutoff, *policy_parameters],
            ).fetchall()

            # margin：两融净买入的截面 percent_rank（*2-1，与 composite 的
            # duckdb RANK NULLS LAST 语义一致）。缺失不计。PIT 取最新 run。
            margin_sql, margin_parameters = self._symbol_filter(selected, alias="ranked")
            margin_rows = connection.execute(
                f"""
                WITH run AS (
                    SELECT snapshot_id, data_cutoff
                    FROM market_snapshot_runs
                    WHERE data_cutoff <= ?
                    ORDER BY snapshot_time DESC
                    LIMIT 1
                ),
                items AS (
                    SELECT i.symbol,
                           CAST(json_extract_string(
                               i.payload_json, '$.margin_net_buy'
                           ) AS DOUBLE) AS margin_net_buy
                    FROM market_snapshot_items i, run
                    WHERE i.snapshot_id = run.snapshot_id
                ),
                ranked AS (
                    SELECT symbol, margin_net_buy,
                           percent_rank() OVER (ORDER BY margin_net_buy) AS pct
                    FROM items
                    WHERE margin_net_buy IS NOT NULL
                )
                SELECT ranked.symbol, ranked.pct * 2 - 1 AS score,
                       run.snapshot_id, run.data_cutoff, ranked.margin_net_buy
                FROM ranked, run
                WHERE 1 = 1
                  {margin_sql}
                """,
                [data_cutoff, *margin_parameters],
            ).fetchall()

        bars_by_symbol: dict[str, list[MarketBar]] = defaultdict(list)
        for row in bar_rows:
            bars_by_symbol[row[1]].append(
                MarketBar(
                    canonical_record_id=row[0],
                    symbol=row[1],
                    event_time=row[2],
                    data_cutoff=row[3],
                    open=row[4],
                    high=row[5],
                    low=row[6],
                    close=row[7],
                    volume=row[8],
                    amount=row[9],
                )
            )
        factor_outputs: dict[str, dict[FactorType, FactorOutput]] = defaultdict(
            dict
        )
        for row in factor_rows:
            factor = FactorOutput(
                factor_id=row[0],
                symbol=row[1],
                factor_type=row[2],
                score=row[3],
                confidence=row[4],
                data_cutoff=row[5],
                generated_at=row[6],
                evidence_ids=_load(row[7]),
                risk_flags=_load(row[8]),
                model_call_ids=_load(row[9]),
                algorithm_version=row[10],
                input_snapshot_hash=row[11],
                shadow_mode=row[12],
                metadata=_load(row[13]),
            )
            factor_outputs[factor.symbol][factor.factor_type] = factor
        sentiment = {
            row[1]: {
                "snapshot_id": row[0],
                "symbol": row[1],
                "data_cutoff": row[2],
                "score": row[3],
                "confidence": row[4],
                "evidence_ids": _load(row[5]),
                "risk_flags": _load(row[6]),
                "input_snapshot_hash": row[7],
                "algorithm_version": row[8],
                "generated_at": row[9],
                "shadow_mode": row[10],
            }
            for row in sentiment_rows
        }
        policy = {
            row[1]: {
                "snapshot_id": row[0],
                "symbol": row[1],
                "data_cutoff": row[2],
                "score": row[3],
                "confidence": row[4],
                "evidence_ids": _load(row[5]),
                "risk_flags": _load(row[6]),
                "input_snapshot_hash": row[7],
                "algorithm_version": row[8],
                "generated_at": row[9],
                "shadow_mode": row[10],
                "shared_sentiment_event_ids": _load(row[11]),
            }
            for row in policy_rows
        }
        generated_at = datetime.now().astimezone()
        margin = {
            row[0]: {
                "snapshot_id": row[2],
                "symbol": row[0],
                "data_cutoff": row[3],
                "score": float(row[1]),
                "confidence": 1.0,
                "evidence_ids": [f"mkt_snapshot:{row[2]}:{row[0]}"],
                "risk_flags": [],
                "input_snapshot_hash": _margin_hash(
                    row[2], row[0], row[1], row[3]
                ),
                "algorithm_version": "margin-snapshot-v1",
                "generated_at": generated_at,
                "shadow_mode": True,
            }
            for row in margin_rows
        }
        return BulkFactorData(
            symbols=selected,
            bars_by_symbol=dict(bars_by_symbol),
            factor_outputs=dict(factor_outputs),
            sentiment_snapshots=sentiment,
            policy_news_snapshots=policy,
            margin_snapshots=margin,
        )

    def load_formal_risk_input(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
    ) -> FormalRiskInput:
        with get_connection() as connection:
            universe = connection.execute(
                """
                SELECT listing_status, delist_date, is_st, is_suspended,
                       verification_status, data_available_time
                FROM stock_universe
                WHERE symbol = ?
                  AND data_available_time <= ?
                ORDER BY data_available_time DESC, updated_at DESC,
                         universe_version DESC
                LIMIT 1
                """,
                [symbol, data_cutoff],
            ).fetchone()
            event_rows = connection.execute(
                """
                SELECT c.event_cluster_id, c.canonical_title, c.event_type,
                       c.event_time, d.fetched_at, c.data_cutoff
                FROM event_clusters c
                JOIN event_symbol_links link
                  ON link.event_cluster_id = c.event_cluster_id
                JOIN data_records d
                  ON d.record_id = c.primary_source_id
                WHERE link.symbol = ?
                  AND c.event_time <= ?
                  AND c.data_cutoff <= ?
                  AND d.fetched_at <= ?
                ORDER BY c.event_time, c.event_cluster_id
                """,
                [symbol, data_cutoff, data_cutoff, data_cutoff],
            ).fetchall()
            sentiment = connection.execute(
                """
                SELECT missing_fields_json
                FROM sentiment_symbol_snapshots
                WHERE symbol = ?
                  AND analysis_mode = 'DECISION'
                  AND data_cutoff <= ?
                ORDER BY data_cutoff DESC, generated_at DESC, snapshot_id
                LIMIT 1
                """,
                [symbol, data_cutoff],
            ).fetchone()
            policy = connection.execute(
                """
                SELECT missing_fields_json
                FROM policy_news_symbol_snapshots
                WHERE symbol = ?
                  AND analysis_mode = 'DECISION'
                  AND data_cutoff <= ?
                ORDER BY data_cutoff DESC, generated_at DESC, snapshot_id
                LIMIT 1
                """,
                [symbol, data_cutoff],
            ).fetchone()
        events = tuple(
            FormalRiskEvent(
                event_id=row[0],
                title=row[1],
                risk_category=row[2],
                event_time=row[3],
                data_available_time=row[4],
                data_cutoff=row[5],
            )
            for row in event_rows
        )
        event_coverage_complete = (
            sentiment is not None
            and policy is not None
            and not _load(sentiment[0])
            and not _load(policy[0])
        )
        status_complete = (
            universe is not None
            and str(universe[4]).upper() != "CONFLICT"
            and universe[5] <= data_cutoff
        )
        return FormalRiskInput(
            events=events,
            suspended=(bool(universe[3]) if universe is not None else None),
            delisted=(
                bool(
                    universe[0] != "ACTIVE"
                    or universe[1] is not None
                    and universe[1] <= data_cutoff.date()
                )
                if universe is not None
                else None
            ),
            st_status=(bool(universe[2]) if universe is not None else None),
            event_coverage_complete=event_coverage_complete,
            security_status_complete=status_complete,
            requested_cutoff=data_cutoff,
        )

    def save_factor(self, factor: FactorOutput) -> FactorOutput:
        return self.factor_repository.save(factor)

    def save_bundle_and_composite(
        self,
        bundle: FactorBundle,
        composite: ShadowComposite,
    ) -> bool:
        persisted_factor = composite.factor_output
        bundle_id = "fbs_" + bundle.input_snapshot_hash[:32]
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                existing = connection.execute(
                    """
                    SELECT composite_id
                    FROM shadow_composite_snapshots
                    WHERE composite_id = ?
                    """,
                    [persisted_factor.factor_id],
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO factor_bundle_snapshots (
                        bundle_id, symbol, analysis_mode, data_cutoff,
                        input_snapshot_hash, available_factor_types_json,
                        missing_factor_types_json, stale_factor_types_json,
                        conflicting_factor_types_json, evidence_ids_json,
                        shared_event_cluster_ids_json,
                        shared_evidence_groups_json, risk_flags_json,
                        payload_json, algorithm_version, generated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        bundle_id,
                        bundle.symbol,
                        bundle.analysis_mode.value,
                        bundle.data_cutoff,
                        bundle.input_snapshot_hash,
                        _dump(
                            [
                                item.value
                                for item in bundle.available_factor_types
                            ]
                        ),
                        _dump(
                            [
                                item.value
                                for item in bundle.missing_factor_types
                            ]
                        ),
                        _dump(
                            [
                                item.value
                                for item in bundle.stale_factor_types
                            ]
                        ),
                        _dump(
                            [
                                item.value
                                for item in bundle.conflicting_factor_types
                            ]
                        ),
                        _dump(bundle.evidence_ids),
                        _dump(bundle.shared_event_cluster_ids),
                        _dump(
                            [
                                group.model_dump(mode="json")
                                for group in bundle.shared_evidence_groups
                            ]
                        ),
                        _dump([flag.value for flag in bundle.risk_flags]),
                        bundle.model_dump_json(),
                        composite.algorithm_version,
                        composite.factor_output.generated_at,
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO shadow_composite_snapshots (
                        composite_id, symbol, analysis_mode, data_cutoff,
                        factor_output_id, input_snapshot_hash, score,
                        confidence, source_factor_output_ids_json,
                        shared_evidence_groups_json, effective_weights_json,
                        correlation_discounts_json,
                        missing_factor_types_json, stale_factor_types_json,
                        risk_flags_json, shadow_mode,
                        formal_strategy_weight, algorithm_version,
                        generated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        persisted_factor.factor_id,
                        bundle.symbol,
                        bundle.analysis_mode.value,
                        bundle.data_cutoff,
                        persisted_factor.factor_id,
                        composite.input_snapshot_hash,
                        composite.score,
                        composite.confidence,
                        _dump(composite.source_factor_output_ids),
                        _dump(
                            [
                                group.model_dump(mode="json")
                                for group in composite.shared_evidence_groups
                            ]
                        ),
                        _dump(composite.effective_weights),
                        _dump(
                            [
                                item.model_dump(mode="json")
                                for item in composite.correlation_discounts
                            ]
                        ),
                        _dump(
                            [
                                item.value
                                for item in composite.missing_factor_types
                            ]
                        ),
                        _dump(
                            [
                                item.value
                                for item in composite.stale_factor_types
                            ]
                        ),
                        _dump(
                            [flag.value for flag in composite.risk_flags]
                        ),
                        True,
                        0.0,
                        composite.algorithm_version,
                        composite.factor_output.generated_at,
                    ],
                )
                for discount in composite.correlation_discounts:
                    audit_hash = hashlib.sha256(
                        _dump(
                            {
                                "composite_id": persisted_factor.factor_id,
                                "pair": discount.factor_pair,
                            }
                        ).encode()
                    ).hexdigest()
                    source_ids = discount.source_factor_output_ids
                    connection.execute(
                        """
                        INSERT INTO factor_correlation_audits (
                            correlation_audit_id, composite_id, symbol,
                            analysis_mode, data_cutoff, factor_pair,
                            source_factor_output_id,
                            related_factor_output_id,
                            shared_event_cluster_id, overlap_ratio,
                            applied_discount, shared_evidence_ids_json,
                            payload_json, algorithm_version, generated_at
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            "fca_" + audit_hash[:32],
                            persisted_factor.factor_id,
                            bundle.symbol,
                            bundle.analysis_mode.value,
                            bundle.data_cutoff,
                            discount.factor_pair,
                            source_ids[0],
                            source_ids[1],
                            (
                                discount.shared_event_cluster_ids[0]
                                if discount.shared_event_cluster_ids
                                else None
                            ),
                            discount.overlap_ratio,
                            discount.applied_discount,
                            _dump(discount.shared_evidence_ids),
                            discount.model_dump_json(),
                            discount.algorithm_version,
                            composite.factor_output.generated_at,
                        ],
                    )
                connection.execute("COMMIT")
                return existing is None
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def save_evaluation(self, request: EvaluationRequest) -> tuple[str, bool]:
        identity = hashlib.sha256(
            _dump(
                {
                    "symbol": request.symbol,
                    "analysis_time": request.analysis_time,
                    "input_snapshot_hash": request.input_snapshot_hash,
                }
            ).encode()
        ).hexdigest()
        evaluation_id = "oev_" + identity[:32]
        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT evaluation_id
                FROM orchestration_evaluations
                WHERE evaluation_id = ?
                """,
                [evaluation_id],
            ).fetchone()
            connection.execute(
                """
                INSERT INTO orchestration_evaluations (
                    evaluation_id, symbol, analysis_time, formal_score,
                    formal_action, shadow_score, composite_confidence,
                    factor_coverage_json, effective_weights_json,
                    return_1d, return_3d, return_5d, return_20d,
                    maximum_upside, maximum_drawdown, risk_vetoed,
                    data_complete, input_snapshot_hash, evaluated_at,
                    generated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    evaluation_id,
                    request.symbol,
                    request.analysis_time,
                    request.formal_score,
                    request.formal_action.value,
                    request.shadow_score,
                    request.composite_confidence,
                    request.factor_coverage.model_dump_json(),
                    _dump(request.effective_weights),
                    request.return_1d,
                    request.return_3d,
                    request.return_5d,
                    request.return_20d,
                    request.maximum_upside,
                    request.maximum_drawdown,
                    request.risk_vetoed,
                    request.data_complete,
                    request.input_snapshot_hash,
                    request.evaluated_at,
                    datetime.now().astimezone(),
                ],
            )
        return evaluation_id, existing is None


__all__ = [
    "BulkFactorData",
    "OrchestrationRepository",
]
