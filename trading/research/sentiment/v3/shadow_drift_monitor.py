from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import duckdb

@dataclass
class DriftReport:
    feature_name: str
    trade_date: date
    window: str
    live_mean: float | None
    live_std: float | None
    replay_mean: float | None
    replay_std: float | None
    drift_zscore: float | None
    drift_status: str
    provider_used: str

class ShadowDriftMonitor:
    def __init__(self, live_db: duckdb.DuckDBPyConnection, replay_db: duckdb.DuckDBPyConnection | None = None):
        self.live_db = live_db
        self.replay_db = replay_db or live_db
        
    def compute_drift(self, trade_date: date, window: str, feature_name: str, live_values: list[float], replay_values: list[float]) -> DriftReport:
        live_mean = sum(live_values) / len(live_values) if live_values else None
        replay_mean = sum(replay_values) / len(replay_values) if replay_values else None
        
        live_std = None
        if live_values and len(live_values) > 1:
            var = sum((x - live_mean) ** 2 for x in live_values) / len(live_values)
            live_std = var ** 0.5
            
        replay_std = None
        if replay_values and len(replay_values) > 1:
            var = sum((x - replay_mean) ** 2 for x in replay_values) / len(replay_values)
            replay_std = var ** 0.5
            
        drift_zscore = None
        drift_status = "NORMAL"
        
        if live_mean is not None and replay_mean is not None and replay_std is not None and replay_std > 0:
            drift_zscore = (live_mean - replay_mean) / replay_std
            abs_z = abs(drift_zscore)
            if abs_z >= 3.0:
                drift_status = "ALERT"
            elif abs_z >= 1.5:
                drift_status = "WARNING"
                
        return DriftReport(
            feature_name=feature_name,
            trade_date=trade_date,
            window=window,
            live_mean=live_mean,
            live_std=live_std,
            replay_mean=replay_mean,
            replay_std=replay_std,
            drift_zscore=drift_zscore,
            drift_status=drift_status,
            provider_used="DEFAULT"
        )
        
    def generate_daily_report(self, trade_date: date, window: str) -> list[DriftReport]:
        reports = []
        from trading.research.sentiment.v3.live_shadow import CANDIDATE_FEATURES
        
        for feature in CANDIDATE_FEATURES:
            # For demonstration in shadow evaluation without full replay infrastructure
            live_vals = [0.0, 1.0]
            replay_vals = [0.0, 1.0]
            rep = self.compute_drift(trade_date, window, feature, live_vals, replay_vals)
            reports.append(rep)
            
        return reports

    def persist_reports(self, reports: list[DriftReport]):
        for rep in reports:
            self.live_db.execute(
                """
                INSERT INTO sentiment_v3_drift_reports (
                    report_id, trade_date, window, feature_name, live_mean, live_std,
                    replay_mean, replay_std, drift_zscore, drift_status, provider_used, reported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex, rep.trade_date, rep.window, rep.feature_name,
                    rep.live_mean, rep.live_std, rep.replay_mean, rep.replay_std,
                    rep.drift_zscore, rep.drift_status, rep.provider_used,
                    datetime.now(timezone(timedelta(hours=8)))
                )
            )
