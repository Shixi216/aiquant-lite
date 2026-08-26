from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from config.settings import settings
from trading.research.sentiment.v3.repository import SentimentV3Repository, _dump
from trading.research.sentiment.v3.schemas import RecordSource


_CREATE_FORWARD_LABEL_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_v3_forward_labels (
    label_id VARCHAR PRIMARY KEY,
    feature_packet_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    signal_trade_date DATE NOT NULL,
    feature_cutoff TIMESTAMPTZ NOT NULL,
    label_name VARCHAR NOT NULL,
    label_kind VARCHAR NOT NULL,
    label_horizon INTEGER NOT NULL,
    record_source VARCHAR NOT NULL,
    label_status VARCHAR NOT NULL,
    adjusted_return DOUBLE,
    label_spec_hash VARCHAR NOT NULL,
    price_data_version VARCHAR NOT NULL,
    label_algorithm_version VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(feature_packet_id, label_spec_hash, price_data_version, content_hash)
)
"""

_CREATE_EVALUATION_DATASET_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_v3_evaluation_datasets (
    dataset_id VARCHAR PRIMARY KEY,
    dataset_version VARCHAR NOT NULL,
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    record_source VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL UNIQUE,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
)
"""


class SentimentV3EvaluationRepository(SentimentV3Repository):
    """S3A storage extension over the existing Sentiment V3 DuckDB boundary."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        super().__init__(database_path or settings.opc_database_path)
        with self._connection() as connection:
            connection.execute(_CREATE_FORWARD_LABEL_TABLE)
            connection.execute(_CREATE_EVALUATION_DATASET_TABLE)

    def save_forward_labels(self, labels: Iterable[Any]) -> list[Any]:
        values = list({item.label_id: item for item in labels}.values())
        if not values:
            return []
        import pandas as pd

        rows = [
            [
                item.label_id,
                item.feature_packet_id,
                item.symbol,
                item.signal_trade_date,
                item.feature_cutoff,
                item.label_name,
                item.label_kind.value,
                item.label_horizon,
                item.record_source.value,
                item.label_status.value,
                item.adjusted_return,
                item.label_spec_hash,
                item.price_data_version,
                item.label_algorithm_version,
                item.content_hash,
                _dump(item.model_dump(mode="json")),
                item.created_at,
            ]
            for item in values
        ]
        columns = [
            "label_id", "feature_packet_id", "symbol", "signal_trade_date",
            "feature_cutoff", "label_name", "label_kind", "label_horizon",
            "record_source", "label_status", "adjusted_return", "label_spec_hash",
            "price_data_version", "label_algorithm_version", "content_hash",
            "payload_json", "created_at",
        ]
        with self._connection() as connection:
            connection.register("_sentiment_v3_label_batch", pd.DataFrame(rows, columns=columns))
            try:
                connection.execute(
                    """
                    INSERT INTO sentiment_v3_forward_labels SELECT *
                    FROM _sentiment_v3_label_batch ON CONFLICT DO NOTHING
                    """
                )
            finally:
                connection.unregister("_sentiment_v3_label_batch")
        return values

    def get_forward_label(self, label_id: str) -> Any | None:
        from trading.research.sentiment.v3.labels import ForwardReturnLabel

        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM sentiment_v3_forward_labels WHERE label_id = ?",
                [label_id],
            ).fetchone()
        return None if row is None else ForwardReturnLabel.model_validate(json.loads(row[0]))

    def list_forward_labels(
        self,
        *,
        record_source: RecordSource = RecordSource.REAL,
        packet_ids: Iterable[str] | None = None,
        limit: int = 10000,
    ) -> list[Any]:
        from trading.research.sentiment.v3.labels import ForwardReturnLabel

        values = sorted(set(packet_ids or []))
        clauses = ["record_source = ?"]
        parameters: list[Any] = [record_source.value]
        if values:
            clauses.append(f"feature_packet_id IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json FROM sentiment_v3_forward_labels
                WHERE {' AND '.join(clauses)}
                ORDER BY feature_cutoff, symbol, label_horizon, label_name LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [ForwardReturnLabel.model_validate(json.loads(row[0])) for row in rows]

    def save_evaluation_dataset(self, dataset: Any) -> Any:
        definition = dataset.definition
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_v3_evaluation_datasets (
                    dataset_id, dataset_version, date_from, date_to,
                    record_source, content_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
                """,
                [
                    definition.dataset_id,
                    definition.dataset_version,
                    definition.date_from,
                    definition.date_to,
                    definition.record_source.value,
                    definition.content_hash,
                    _dump(dataset.model_dump(mode="json")),
                    definition.created_at,
                ],
            )
        return dataset


__all__ = ["SentimentV3EvaluationRepository"]
