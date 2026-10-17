from __future__ import annotations

import hashlib
import json
import uuid
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from time import perf_counter
from typing import Any

import duckdb

try:
    from trading.research.sentiment.v3.hashing import canonical_json
except ImportError:
    def canonical_json(data: Any) -> str:
        return json.dumps(data, sort_keys=True, separators=(',', ':'))

LIVE_SHADOW_ALGORITHM_VERSION = "sentiment-v3-live-shadow-1.0.0"

CANDIDATE_FEATURES = (
    "capital.dragon_tiger.institution_net_amount",
    "capital.dragon_tiger.net_amount",
    "capital.stock.turnover",
    "stock.gap_and_follow_through",
)

FEATURE_DIRECTIONS = {
    "capital.dragon_tiger.institution_net_amount": 1,
    "capital.dragon_tiger.net_amount": 1,
    "capital.stock.turnover": -1,
    "stock.gap_and_follow_through": -1,
}

FEATURE_COLUMNS = {
    "capital.dragon_tiger.institution_net_amount": "capital__dragon_tiger__institution_net_amount",
    "capital.dragon_tiger.net_amount": "capital__dragon_tiger__net_amount",
    "capital.stock.turnover": "capital__stock__turnover",
    "stock.gap_and_follow_through": "stock__gap_and_follow_through",
}

class ShadowWindow(StrEnum):
    PREOPEN = "PREOPEN"
    POSTCLOSE = "POSTCLOSE"

class RiskReminderStatus(StrEnum):
    TRIGGERED = "TRIGGERED"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

@dataclass
class LiveShadowResult:
    observation_id: str
    run_id: str
    symbol: str
    trade_date: date
    cutoff: datetime
    window: ShadowWindow
    research_score: float | None
    confidence: float
    risk_reminder_triggered: bool
    risk_reminder_details: dict[str, Any]
    feature_values: dict[str, float | None]
    feature_quality: dict[str, str]
    provider_latency_ms: float
    data_coverage_ratio: float
    content_hash: str
    formal_weight: float = 0.0
    research_only: bool = True
    creates_trade: bool = False

@dataclass
class LiveShadowRunSummary:
    run_id: str
    window: ShadowWindow
    trade_date: date
    total_symbols: int
    completed_symbols: int
    risk_reminders_triggered: int
    mean_provider_latency_ms: float
    mean_coverage_ratio: float
    elapsed_seconds: float
    formal_weight: float = 0.0
    research_only: bool = True
    creates_trade: bool = False

def compute_equal_weight_composite(cross_section_features: list[dict[str, float | None]], feature_directions: dict[str, int]) -> list[float | None]:
    feature_stats = {}
    for feature in CANDIDATE_FEATURES:
        values = [row[feature] for row in cross_section_features if feature in row and row.get(feature) is not None]
        if values:
            mean = sum(values) / len(values)
            variance = sum((x - mean) ** 2 for x in values) / len(values)
            std = math.sqrt(variance)
            feature_stats[feature] = (mean, std)
        else:
            feature_stats[feature] = (0.0, 0.0)
            
    scores = []
    for row in cross_section_features:
        z_scores = []
        for feature in CANDIDATE_FEATURES:
            val = row.get(feature)
            if val is not None:
                mean, std = feature_stats[feature]
                z = (val - mean) / std if std > 0 else 0.0
                z_scores.append(z * feature_directions[feature])
        scores.append(sum(z_scores) / len(z_scores) if z_scores else None)
    return scores

def evaluate_risk_reminder(feature_values: dict[str, float | None], medians: dict[str, float]) -> tuple[RiskReminderStatus, dict[str, Any]]:
    dt_net = feature_values.get("capital.dragon_tiger.net_amount")
    turnover = feature_values.get("capital.stock.turnover")
    gap = feature_values.get("stock.gap_and_follow_through")
    
    if dt_net is None or turnover is None or gap is None:
        return RiskReminderStatus.INSUFFICIENT_DATA, {"reason": "missing_features"}
        
    dt_median = medians.get("capital.dragon_tiger.net_amount", 0.0)
    turnover_median = medians.get("capital.stock.turnover", 0.0)
    gap_median = medians.get("stock.gap_and_follow_through", 0.0)
    
    triggered = dt_net < dt_median and turnover > turnover_median and gap < gap_median
    
    details = {
        "dt_net": dt_net, "dt_median": dt_median,
        "turnover": turnover, "turnover_median": turnover_median,
        "gap": gap, "gap_median": gap_median,
    }
    
    return RiskReminderStatus.TRIGGERED if triggered else RiskReminderStatus.NOT_TRIGGERED, details

class LiveShadowExecutor:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.conn = connection
        
    def _get_content_hash(self, symbol: str, trade_date: date, cutoff: datetime, feature_values: dict) -> str:
        data = {
            "symbol": symbol,
            "trade_date": trade_date.isoformat(),
            "cutoff": cutoff.isoformat(),
            "feature_values": feature_values
        }
        json_str = canonical_json(data)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()
        
    def run_single(self, symbol: str, trade_date: date, cutoff: datetime, window: ShadowWindow, feature_values: dict[str, float | None], medians: dict[str, float], run_id: str, score: float | None) -> LiveShadowResult:
        status, details = evaluate_risk_reminder(feature_values, medians)
        
        valid_features = sum(1 for v in feature_values.values() if v is not None)
        coverage = valid_features / len(CANDIDATE_FEATURES) if CANDIDATE_FEATURES else 0.0
        content_hash = self._get_content_hash(symbol, trade_date, cutoff, feature_values)
        
        result = LiveShadowResult(
            observation_id=uuid.uuid4().hex, run_id=run_id, symbol=symbol,
            trade_date=trade_date, cutoff=cutoff, window=window, research_score=score,
            confidence=coverage, risk_reminder_triggered=(status == RiskReminderStatus.TRIGGERED),
            risk_reminder_details=details, feature_values=feature_values,
            feature_quality={k: "OK" if v is not None else "MISSING" for k, v in feature_values.items()},
            provider_latency_ms=10.0, data_coverage_ratio=coverage, content_hash=content_hash,
        )
        self.persist_observation(result)
        return result
        
    def run_batch(self, trade_date: date, cutoff: datetime, window: ShadowWindow, batch_data: list[dict]) -> LiveShadowRunSummary:
        start_time = perf_counter()
        run_id = uuid.uuid4().hex
        
        cross_section_features = [d["feature_values"] for d in batch_data]
        scores = compute_equal_weight_composite(cross_section_features, FEATURE_DIRECTIONS)
        
        medians = {}
        for feature in CANDIDATE_FEATURES:
            values = sorted([row[feature] for row in cross_section_features if feature in row and row.get(feature) is not None])
            if values:
                mid = len(values) // 2
                medians[feature] = (values[mid - 1] + values[mid]) / 2.0 if len(values) % 2 == 0 else values[mid]
            else:
                medians[feature] = 0.0
                
        results = []
        for item, score in zip(batch_data, scores):
            res = self.run_single(
                symbol=item["symbol"], trade_date=trade_date, cutoff=cutoff,
                window=window, feature_values=item["feature_values"],
                medians=medians, run_id=run_id, score=score
            )
            results.append(res)
            
        elapsed = perf_counter() - start_time
        
        return LiveShadowRunSummary(
            run_id=run_id, window=window, trade_date=trade_date,
            total_symbols=len(batch_data), completed_symbols=len(results),
            risk_reminders_triggered=sum(1 for r in results if r.risk_reminder_triggered),
            mean_provider_latency_ms=sum(r.provider_latency_ms for r in results) / len(results) if results else 0.0,
            mean_coverage_ratio=sum(r.data_coverage_ratio for r in results) / len(results) if results else 0.0,
            elapsed_seconds=elapsed
        )
        
    def persist_observation(self, result: LiveShadowResult):
        self.conn.execute(
            """
            INSERT INTO sentiment_v3_live_shadow_observations (
                observation_id, run_id, symbol, trade_date, cutoff, "window",
                research_score, confidence, risk_reminder_triggered, risk_reminder_details,
                feature_values, feature_quality, provider_latency_ms, data_coverage_ratio,
                content_hash, formal_weight, research_only, creates_trade, observed_at, algorithm_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.observation_id, result.run_id, result.symbol, result.trade_date, result.cutoff, result.window,
                result.research_score, result.confidence, result.risk_reminder_triggered, json.dumps(result.risk_reminder_details),
                json.dumps(result.feature_values), json.dumps(result.feature_quality), result.provider_latency_ms,
                result.data_coverage_ratio, result.content_hash, result.formal_weight, result.research_only, result.creates_trade,
                datetime.now(timezone(timedelta(hours=8))), LIVE_SHADOW_ALGORITHM_VERSION
            )
        )
