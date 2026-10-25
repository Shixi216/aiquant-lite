from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


PERFORMANCE_FIELDS = (
    "db_connect_ms",
    "db_query_ms",
    "bars_ms",
    "financial_ms",
    "announcement_ms",
    "news_ms",
    "sentiment_ms",
    "technical_ms",
    "fundamental_ms",
    "capital_ms",
    "policy_ms",
    "decision_ms",
    "render_ms",
)
SLOW_STAGE_MS = 5_000.0
SLOW_REPORT_MS = 30_000.0


def _performance_log_path() -> Path:
    return Path(__file__).resolve().parents[2] / "logs" / "stock-report-performance.jsonl"


def _write_record(payload: dict[str, object]) -> None:
    # Pytest subprocesses inherit PYTEST_CURRENT_TEST. Never leave test
    # diagnostics mixed with real runtime observations.
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    try:
        target = _performance_log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        logging.getLogger(__name__).warning("Stock-report performance log unavailable")


@dataclass
class StockReportPerformance:
    request_id: str
    symbol: str
    mode: str
    no_fetch: bool
    database_owner_pid: int
    _started: float = field(default_factory=time.perf_counter)
    _stages: dict[str, float] = field(default_factory=dict)
    _error_type: str | None = field(default=None, init=False)

    def record_failure(self, exc: Exception) -> None:
        # Store the class only; provider errors may contain credentials or prompts.
        self._error_type = type(exc).__name__

    def record(self, stage: str, elapsed_ms: float) -> None:
        self._stages[stage] = self._stages.get(stage, 0.0) + max(0.0, elapsed_ms)

    def measure(self, stage: str):
        performance = self

        class _Measure:
            def __enter__(self) -> None:
                self.started = time.perf_counter()

            def __exit__(self, exc_type, exc, traceback) -> None:
                performance.record(
                    stage,
                    (time.perf_counter() - self.started) * 1_000.0,
                )

        return _Measure()

    def finish(self) -> dict[str, object]:
        total_ms = max(0.0, (time.perf_counter() - self._started) * 1_000.0)
        timestamp = datetime.now(timezone.utc).isoformat()
        payload: dict[str, object] = {
            "event": "STOCK_REPORT_PERFORMANCE",
            "timestamp": timestamp,
            "success": self._error_type is None,
            "error_type": self._error_type,
            "request_id": self.request_id,
            "symbol": self.symbol,
            "mode": self.mode,
            "no_fetch": self.no_fetch,
            "total_ms": round(total_ms, 3),
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "database_owner_pid": self.database_owner_pid,
        }
        for field_name in PERFORMANCE_FIELDS:
            payload[field_name] = round(self._stages.get(field_name, 0.0), 3)
        _write_record(payload)
        for field_name in PERFORMANCE_FIELDS:
            elapsed_ms = float(payload[field_name])
            if elapsed_ms > SLOW_STAGE_MS:
                _write_record(
                    {
                        "event": "SLOW_STAGE",
                        "timestamp": timestamp,
                        "request_id": self.request_id,
                        "symbol": self.symbol,
                        "stage": field_name,
                        "elapsed_ms": elapsed_ms,
                        "pid": os.getpid(),
                        "thread_id": threading.get_ident(),
                        "database_owner_pid": self.database_owner_pid,
                    }
                )
        if total_ms > SLOW_REPORT_MS:
            _write_record(
                {
                    "event": "SLOW_STOCK_REPORT",
                    "timestamp": timestamp,
                    "request_id": self.request_id,
                    "symbol": self.symbol,
                    "total_ms": round(total_ms, 3),
                    "pid": os.getpid(),
                    "thread_id": threading.get_ident(),
                    "database_owner_pid": self.database_owner_pid,
                }
            )
        return payload


__all__ = [
    "PERFORMANCE_FIELDS",
    "SLOW_REPORT_MS",
    "SLOW_STAGE_MS",
    "StockReportPerformance",
]
