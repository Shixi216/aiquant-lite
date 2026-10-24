"""Wealth Lab API 契约 v1 的 Pydantic 模型（与 docs/contracts/wealth-lab-api-v1.yaml 一致）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "wealth-lab/v1"


class LabModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def new_request_id() -> str:
    import uuid

    return "req_" + uuid.uuid4().hex[:24]


def envelope(
    data: Any,
    *,
    status: str = "SUCCESS",
    error_code: str | None = None,
    sanitized_error: str | None = None,
    warnings: list[str] | None = None,
    risk_flags: list[str] | None = None,
    data_cutoff: datetime | None = None,
    api_version: str = API_VERSION,
) -> dict:
    return {
        "request_id": new_request_id(),
        "status": status,
        "data": data,
        "error_code": error_code,
        "sanitized_error": sanitized_error,
        "warnings": warnings or [],
        "risk_flags": risk_flags or [],
        "data_cutoff": data_cutoff,
        "generated_at": datetime.now().astimezone(),
        "api_version": api_version,
        "research_only": True,
    }


class BacktestRequest(LabModel):
    weights: dict[str, float] | None = None
    start_date: date | None = None
    end_date: date | None = None
    rebalance: Literal["monthly", "quarterly", "none"] = "monthly"
    cost_bps: float = Field(default=5.0, ge=0, le=500)
    initial_capital: float = Field(default=1_000_000.0, gt=0)
    benchmarks: list[str] = Field(default_factory=lambda: ["EQUAL_WEIGHT", "510300.SH"])
    allow_demo: bool = False


class HistoricalScenario(LabModel):
    kind: Literal["historical"]
    label: str
    start_date: date
    end_date: date


class SyntheticScenario(LabModel):
    kind: Literal["synthetic"]
    label: str
    shocks: dict[str, float]


class StressTestRequest(LabModel):
    scenario: HistoricalScenario | SyntheticScenario
    weights: dict[str, float] | None = None
