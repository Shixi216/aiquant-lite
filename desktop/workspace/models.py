from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from router.integration.skills import SkillResultCard


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_DATA = "WAITING_DATA"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


class TimelineKind(StrEnum):
    USER_INPUT = "USER_INPUT"
    QUERY_PARSE = "QUERY_PARSE"
    SKILL_SELECTED = "SKILL_SELECTED"
    TOOL_CALL = "TOOL_CALL"
    DATA_READ = "DATA_READ"
    STRUCTURED_RESULT = "STRUCTURED_RESULT"
    MISSING_DATA = "MISSING_DATA"
    RISK_FLAG = "RISK_FLAG"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ConversationContext(WorkspaceModel):
    conversation_id: str
    user_message_id: str | None = None
    active_symbol: str | None = None
    compared_symbols: list[str] = Field(default_factory=list)
    last_scanner_query_plan: dict[str, Any] | None = None
    last_scanner_run_id: str | None = None
    selected_candidates: list[str] = Field(default_factory=list, max_length=30)
    current_skill: str | None = None
    current_task_id: str | None = None
    pending_confirmation: str | None = None
    data_cutoff: datetime
    analysis_mode: str = "SCREENING"
    created_at: datetime
    updated_at: datetime

    @field_validator("data_cutoff", "created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("workspace timestamps must include timezone")
        return value


class Conversation(WorkspaceModel):
    conversation_id: str
    title: str
    pinned: bool
    created_at: datetime
    updated_at: datetime


class Message(WorkspaceModel):
    message_id: str
    conversation_id: str
    role: Literal["USER", "SYSTEM"]
    content: str
    request_id: str | None = None
    created_at: datetime


class TaskRun(WorkspaceModel):
    task_id: str
    conversation_id: str
    request_id: str = Field(pattern=r"^req_[0-9a-f]{24}$")
    skill_id: str
    status: TaskStatus
    idempotency_key: str
    parent_task_id: str | None = None
    input_payload: dict[str, Any]
    result_card: SkillResultCard | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskStep(WorkspaceModel):
    step_id: str
    task_id: str
    sequence: int = Field(ge=1)
    kind: TimelineKind
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class QueryPlanChange(WorkspaceModel):
    field: str
    before: Any
    after: Any


class QueryPlanModification(WorkspaceModel):
    plan: dict[str, Any] | None
    changes: list[QueryPlanChange]
    clarification_required: bool = False
    clarification_question: str | None = None


__all__ = [
    "Conversation",
    "ConversationContext",
    "Message",
    "QueryPlanChange",
    "QueryPlanModification",
    "TaskRun",
    "TaskStatus",
    "TaskStep",
    "TimelineKind",
]
