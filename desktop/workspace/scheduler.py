from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Callable
from uuid import uuid4

from desktop.workspace.safety import safe_json_dumps, sanitize_user_visible_text
from desktop.workspace.state import DesktopStateRepository


class ScheduleType(StrEnum):
    MARKET_SCAN = "MARKET_SCAN"
    WATCHLIST_CHECK = "WATCHLIST_CHECK"
    STOCK_RESEARCH = "STOCK_RESEARCH"
    PREMARKET_BRIEF = "PREMARKET_BRIEF"
    DAILY_REVIEW = "DAILY_REVIEW"
    EXPERIMENT_LABEL_UPDATE = "EXPERIMENT_LABEL_UPDATE"
    EXPERIMENT_REPORT = "EXPERIMENT_REPORT"
    SYSTEM_HEALTH = "SYSTEM_HEALTH"
    MARKET_DATA_REFRESH = "MARKET_DATA_REFRESH"
    POSITION_ANNOUNCEMENT_RISK = "POSITION_ANNOUNCEMENT_RISK"


class Frequency(StrEnum):
    ONCE = "ONCE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    TRADING_WINDOW = "TRADING_WINDOW"


FORBIDDEN_AUTOMATION_KEYS = {
    "decision",
    "manual_trade",
    "position_mutation",
    "real_order",
    "formal_weight",
    "database_delete",
    "database_restore",
}


@dataclass(frozen=True, slots=True)
class Schedule:
    schedule_id: str
    name: str
    task_type: ScheduleType
    frequency: Frequency
    payload: dict[str, Any]
    enabled: bool
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ScheduleRun:
    run_id: str
    schedule_id: str
    scheduled_for: datetime
    status: str
    result: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


BUILTIN_TEMPLATES = (
    ("builtin_market_scan_0935", "每天9:35全市场异动扫描", ScheduleType.MARKET_SCAN, Frequency.DAILY),
    ("builtin_daily_review", "每天收盘后复盘", ScheduleType.DAILY_REVIEW, Frequency.DAILY),
    ("builtin_premarket_watchlist", "每天盘前观察池检查", ScheduleType.WATCHLIST_CHECK, Frequency.DAILY),
    ("builtin_weekly_experiment", "每周实验报告", ScheduleType.EXPERIMENT_REPORT, Frequency.WEEKLY),
    ("builtin_position_announcement", "持仓公告风险检查", ScheduleType.POSITION_ANNOUNCEMENT_RISK, Frequency.DAILY),
    ("builtin_system_health", "系统健康检查", ScheduleType.SYSTEM_HEALTH, Frequency.DAILY),
)


class ScheduleRepository:
    def __init__(self, state: DesktopStateRepository) -> None:
        self.state = state
        with self.state._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_runs (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL
                        REFERENCES local_schedules(schedule_id) ON DELETE CASCADE,
                    scheduled_for TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )
        self._install_builtin_templates()

    def _install_builtin_templates(self) -> None:
        now = datetime.now().astimezone().isoformat()
        with self.state._connect() as connection:
            for schedule_id, name, task_type, frequency in BUILTIN_TEMPLATES:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO local_schedules
                    (schedule_id,name,task_type,frequency,payload_json,enabled,
                     next_run_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,0,NULL,?,?)
                    """,
                    [
                        schedule_id,
                        name,
                        task_type.value,
                        frequency.value,
                        "{}",
                        now,
                        now,
                    ],
                )

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> str:
        def keys(value: Any) -> set[str]:
            if isinstance(value, dict):
                return {
                    str(key).casefold()
                    for key in value
                } | {
                    nested
                    for child in value.values()
                    for nested in keys(child)
                }
            if isinstance(value, (list, tuple)):
                return {
                    nested
                    for child in value
                    for nested in keys(child)
                }
            return set()

        lowered_keys = keys(payload)
        if lowered_keys & FORBIDDEN_AUTOMATION_KEYS:
            raise ValueError("forbidden scheduled operation")
        return safe_json_dumps(payload)

    def create(
        self,
        *,
        name: str,
        task_type: ScheduleType,
        frequency: Frequency,
        payload: dict[str, Any] | None = None,
        enabled: bool = False,
        next_run_at: datetime | None = None,
    ) -> Schedule:
        serialized = self._validate_payload(payload or {})
        now = datetime.now().astimezone()
        schedule = Schedule(
            schedule_id="sched_" + uuid4().hex[:24],
            name=sanitize_user_visible_text(name).strip()[:120],
            task_type=task_type,
            frequency=frequency,
            payload=payload or {},
            enabled=enabled,
            next_run_at=next_run_at,
            created_at=now,
            updated_at=now,
        )
        with self.state._connect() as connection:
            connection.execute(
                """
                INSERT INTO local_schedules
                (schedule_id,name,task_type,frequency,payload_json,enabled,
                 next_run_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                [
                    schedule.schedule_id,
                    schedule.name,
                    task_type.value,
                    frequency.value,
                    serialized,
                    int(enabled),
                    next_run_at.isoformat() if next_run_at else None,
                    now.isoformat(),
                    now.isoformat(),
                ],
            )
        return schedule

    def list(self) -> list[Schedule]:
        with self.state._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM local_schedules ORDER BY created_at"
            ).fetchall()
        return [self._schedule(row) for row in rows]

    def set_enabled(self, schedule_id: str, enabled: bool) -> Schedule:
        with self.state._connect() as connection:
            changed = connection.execute(
                """
                UPDATE local_schedules SET enabled=?, updated_at=?
                WHERE schedule_id=?
                """,
                [int(enabled), datetime.now().astimezone().isoformat(), schedule_id],
            ).rowcount
        if not changed:
            raise KeyError("schedule not found")
        return self.get(schedule_id)

    def update_next_run(
        self,
        schedule_id: str,
        *,
        next_run_at: datetime | None,
        enabled: bool | None = None,
    ) -> Schedule:
        assignments = ["next_run_at=?", "updated_at=?"]
        values: list[Any] = [
            next_run_at.isoformat() if next_run_at else None,
            datetime.now().astimezone().isoformat(),
        ]
        if enabled is not None:
            assignments.append("enabled=?")
            values.append(int(enabled))
        values.append(schedule_id)
        with self.state._connect() as connection:
            changed = connection.execute(
                f"""
                UPDATE local_schedules SET {", ".join(assignments)}
                WHERE schedule_id=?
                """,
                values,
            ).rowcount
        if not changed:
            raise KeyError("schedule not found")
        return self.get(schedule_id)

    def get(self, schedule_id: str) -> Schedule:
        with self.state._connect() as connection:
            row = connection.execute(
                "SELECT * FROM local_schedules WHERE schedule_id=?",
                [schedule_id],
            ).fetchone()
        if row is None:
            raise KeyError("schedule not found")
        return self._schedule(row)

    def runs(self, schedule_id: str) -> list[ScheduleRun]:
        with self.state._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM schedule_runs
                WHERE schedule_id=? ORDER BY created_at DESC
                """,
                [schedule_id],
            ).fetchall()
        return [self._run(row) for row in rows]

    def get_run(self, run_id: str) -> ScheduleRun:
        with self.state._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedule_runs WHERE run_id=?",
                [run_id],
            ).fetchone()
        if row is None:
            raise KeyError("schedule run not found")
        return self._run(row)

    def cancel_run(self, run_id: str) -> ScheduleRun:
        with self.state._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedule_runs WHERE run_id=?", [run_id]
            ).fetchone()
            if row is None:
                raise KeyError("schedule run not found")
            if row["status"] in {"SUCCESS", "FAILED", "CANCELLED"}:
                return self._run(row)
            connection.execute(
                """
                UPDATE schedule_runs
                SET status='CANCELLED', completed_at=?
                WHERE run_id=?
                """,
                [datetime.now().astimezone().isoformat(), run_id],
            )
        return self.get_run(run_id)

    @staticmethod
    def _schedule(row: Any) -> Schedule:
        return Schedule(
            schedule_id=row["schedule_id"],
            name=row["name"],
            task_type=ScheduleType(row["task_type"]),
            frequency=Frequency(row["frequency"]),
            payload=json.loads(row["payload_json"]),
            enabled=bool(row["enabled"]),
            next_run_at=(
                datetime.fromisoformat(row["next_run_at"])
                if row["next_run_at"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _run(row: Any) -> ScheduleRun:
        return ScheduleRun(
            run_id=row["run_id"],
            schedule_id=row["schedule_id"],
            scheduled_for=datetime.fromisoformat(row["scheduled_for"]),
            status=row["status"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
        )


ScheduleExecutor = Callable[[ScheduleType, dict[str, Any]], dict[str, Any]]


class LocalScheduler:
    def __init__(
        self,
        repository: ScheduleRepository,
        executor: ScheduleExecutor,
    ) -> None:
        self.repository = repository
        self.executor = executor

    def run_now(
        self,
        schedule_id: str,
        *,
        scheduled_for: datetime | None = None,
    ) -> ScheduleRun:
        schedule = self.repository.get(schedule_id)
        if not schedule.enabled:
            raise ValueError("schedule must be explicitly enabled")
        due = scheduled_for or datetime.now().astimezone()
        idempotency_key = f"{schedule_id}:{due.replace(microsecond=0).isoformat()}"
        with self.repository.state._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM schedule_runs WHERE idempotency_key=?",
                [idempotency_key],
            ).fetchone()
            if existing is not None:
                return self.repository._run(existing)
            run_id = "srun_" + uuid4().hex[:24]
            connection.execute(
                """
                INSERT INTO schedule_runs
                (run_id,schedule_id,scheduled_for,idempotency_key,status,
                 result_json,error_code,created_at,completed_at)
                VALUES(?,?,?,?, 'RUNNING',NULL,NULL,?,NULL)
                """,
                [
                    run_id,
                    schedule_id,
                    due.isoformat(),
                    idempotency_key,
                    datetime.now().astimezone().isoformat(),
                ],
            )
        try:
            result = self.executor(schedule.task_type, schedule.payload)
            result = {
                **result,
                "decision_called": False,
                "manual_trade_written": False,
                "real_order_created": False,
            }
            serialized = safe_json_dumps(result)
            status, error_code = "SUCCESS", None
        except Exception:
            serialized = None
            status, error_code = "FAILED", "SCHEDULE_EXECUTION_FAILED"
        completed = datetime.now().astimezone()
        with self.repository.state._connect() as connection:
            connection.execute(
                """
                UPDATE schedule_runs
                SET status=?, result_json=?, error_code=?, completed_at=?
                WHERE run_id=?
                """,
                [
                    status,
                    serialized,
                    error_code,
                    completed.isoformat(),
                    run_id,
                ],
            )
        return self.repository.get_run(run_id)

    def retry_failed(self, run_id: str) -> ScheduleRun:
        with self.repository.state._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedule_runs WHERE run_id=?", [run_id]
            ).fetchone()
        if row is None or row["status"] != "FAILED":
            raise ValueError("only failed schedule runs can be retried")
        return self.run_now(
            row["schedule_id"],
            scheduled_for=datetime.now().astimezone() + timedelta(seconds=1),
        )

    def run_due(self, *, now: datetime | None = None) -> list[ScheduleRun]:
        checked_at = now or datetime.now().astimezone()
        completed: list[ScheduleRun] = []
        for schedule in self.repository.list():
            if (
                not schedule.enabled
                or schedule.next_run_at is None
                or schedule.next_run_at > checked_at
            ):
                continue
            completed.append(
                self.run_now(
                    schedule.schedule_id,
                    scheduled_for=schedule.next_run_at,
                )
            )
            if schedule.frequency == Frequency.ONCE:
                self.repository.update_next_run(
                    schedule.schedule_id,
                    next_run_at=None,
                    enabled=False,
                )
            else:
                interval = (
                    timedelta(days=7)
                    if schedule.frequency == Frequency.WEEKLY
                    else timedelta(days=1)
                )
                next_run = schedule.next_run_at
                while next_run <= checked_at:
                    next_run += interval
                self.repository.update_next_run(
                    schedule.schedule_id,
                    next_run_at=next_run,
                )
        return completed


__all__ = [
    "BUILTIN_TEMPLATES",
    "Frequency",
    "LocalScheduler",
    "Schedule",
    "ScheduleRepository",
    "ScheduleRun",
    "ScheduleType",
]
