from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from config.settings import settings
from trading.scanner.capital_features import compute_capital_features
from trading.scanner.repository import ScannerRepository
from trading.scanner.technical_features import compute_technical_features


@dataclass(frozen=True)
class ScannerFeatureSet:
    frame: pd.DataFrame
    input_snapshot_hash: str
    snapshot_id: str | None
    snapshot_time: datetime | None
    cold_cache: bool
    data_read_ms: float
    feature_compute_ms: float
    database_session_count: int
    database_query_count: int


class ScannerFeatureLoader:
    """One-query loader with an in-process snapshot-aware bounded cache."""

    _cache: OrderedDict[tuple[str, int, int, str], ScannerFeatureSet] = OrderedDict()
    _cache_size = 4

    def __init__(self, repository: ScannerRepository) -> None:
        self.repository = repository

    @staticmethod
    def _cache_key(data_cutoff: datetime) -> tuple[str, int, int, str]:
        path = Path(settings.opc_database_path).expanduser().resolve()
        stat = path.stat()
        return (
            str(path),
            stat.st_mtime_ns,
            stat.st_size,
            data_cutoff.isoformat(),
        )

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    @staticmethod
    def _finalize(frame: pd.DataFrame, data_cutoff: datetime) -> pd.DataFrame:
        output = frame.copy()
        output["is_suspended"] = (
            output["universe_suspended"].fillna(False)
            | output["snapshot_suspended"].fillna(False)
        )
        output["listing_age_days"] = (
            pd.Timestamp(data_cutoff.date()) - output["list_date"]
        ).dt.days
        output["amplitude"] = (
            (output["current_high"] - output["current_low"])
            / output["previous_close"]
        ).where(output["previous_close"] > 0)
        output["gap_pct"] = (
            output["current_open"] / output["previous_close"] - 1
        ).where(output["previous_close"] > 0)
        output["current_valid"] = (
            output["item_status"].eq("VALID")
            & output["current_price"].gt(0)
            & ~output["is_suspended"]
        )
        snapshot_times = pd.to_datetime(output["snapshot_time"], utc=True)
        cutoff = pd.Timestamp(data_cutoff).tz_convert("UTC")
        output["snapshot_age_seconds"] = (
            cutoff - snapshot_times
        ).dt.total_seconds()
        output["snapshot_stale"] = (
            output["snapshot_age_seconds"]
            > settings.full_market_snapshot_max_age_seconds
        ) | output["snapshot_time"].isna()

        def risk_values(row: object) -> list[str]:
            values: list[str] = []
            for column in (
                "fundamental_risk_flags",
                "sentiment_risk_flags",
                "policy_news_risk_flags",
                "persisted_capital_risk_flags",
                "shadow_risk_flags",
            ):
                raw = getattr(row, column)
                if raw is None or (not isinstance(raw, str) and pd.isna(raw)):
                    continue
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                values.extend(str(item) for item in parsed)
            return list(dict.fromkeys(values))

        output["risk_flags"] = [
            risk_values(row) for row in output.itertuples(index=False)
        ]

        factor_rows: list[dict[str, object]] = []
        configured_weights = {
            "TECHNICAL": 0.30,
            "FUNDAMENTAL": 0.25,
            "SENTIMENT": 0.15,
            "POLICY_NEWS": 0.15,
            "CAPITAL_FLOW": 0.15,
        }
        for row in output.itertuples(index=False):
            values: dict[str, tuple[float, float]] = {}

            def add(name: str, score: object, confidence: object) -> None:
                if (
                    score is None
                    or confidence is None
                    or pd.isna(score)
                    or pd.isna(confidence)
                ):
                    return
                values[name] = (float(score), float(confidence))

            add("TECHNICAL", row.technical_score, row.technical_confidence)
            history_count = (
                0
                if row.history_count is None or pd.isna(row.history_count)
                else int(row.history_count)
            )
            if history_count > 0:
                add(
                    "CAPITAL_FLOW",
                    row.capital_flow_score,
                    row.capital_flow_confidence,
                )
            add(
                "FUNDAMENTAL",
                row.fundamental_score,
                row.fundamental_confidence,
            )
            add("SENTIMENT", row.sentiment_score, row.sentiment_confidence)
            add(
                "POLICY_NEWS",
                row.policy_news_score,
                row.policy_news_confidence,
            )
            weighted = {
                name: configured_weights[name] * confidence
                for name, (_, confidence) in values.items()
            }
            denominator = sum(weighted.values())
            shadow_score = (
                None
                if denominator <= 0
                else sum(
                    values[name][0] * weight
                    for name, weight in weighted.items()
                )
                / denominator
            )
            count = len(values)
            confidence = (
                None
                if denominator <= 0
                else min(
                    1.0,
                    denominator * (count / 5) * min(1.0, count / 3),
                )
            )
            factor_rows.append(
                {
                    "available_factor_names": sorted(values),
                    "factor_coverage_count": count,
                    "factor_coverage_ratio": count / 5,
                    "shadow_composite_score": shadow_score,
                    "composite_confidence": confidence,
                }
            )
        return pd.concat(
            [output.reset_index(drop=True), pd.DataFrame(factor_rows)],
            axis=1,
        )

    def load(self, data_cutoff: datetime) -> ScannerFeatureSet:
        key = self._cache_key(data_cutoff)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return ScannerFeatureSet(
                frame=cached.frame,
                input_snapshot_hash=cached.input_snapshot_hash,
                snapshot_id=cached.snapshot_id,
                snapshot_time=cached.snapshot_time,
                cold_cache=False,
                data_read_ms=0.0,
                feature_compute_ms=0.0,
                database_session_count=0,
                database_query_count=0,
            )
        source = self.repository.load_feature_source(data_cutoff)
        started = perf_counter()
        frame = compute_technical_features(source.frame)
        frame = compute_capital_features(frame)
        frame = self._finalize(frame, data_cutoff)
        feature_ms = (perf_counter() - started) * 1000
        result = ScannerFeatureSet(
            frame=frame,
            input_snapshot_hash=source.input_snapshot_hash,
            snapshot_id=source.snapshot_id,
            snapshot_time=source.snapshot_time,
            cold_cache=True,
            data_read_ms=source.data_read_ms,
            feature_compute_ms=feature_ms,
            database_session_count=source.database_session_count,
            database_query_count=source.database_query_count,
        )
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return result


__all__ = ["ScannerFeatureLoader", "ScannerFeatureSet"]
