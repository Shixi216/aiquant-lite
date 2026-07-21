from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelCallAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    task_id: str | None
    agent_role: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    estimated_cost: float | None
    success: bool
    error_type: str | None
    error_message: str | None
    created_at: datetime


class AgentResultAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    task_id: str
    agent_role: str
    provider: str | None
    model: str | None
    confidence: float | None
    success: bool
    result_json: dict[str, Any] | None
    created_at: datetime


class TaskAuditDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_type: str
    symbol: str | None
    status: str
    request_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    model_calls: list[ModelCallAudit] = Field(
        default_factory=list
    )
    agent_results: list[AgentResultAudit] = Field(
        default_factory=list
    )


class TaskAuditSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_type: str
    symbol: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    model_call_count: int
    result_count: int


class TaskAuditListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    tasks: list[TaskAuditSummary]