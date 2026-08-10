from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from desktop.workspace.models import (
    Conversation,
    ConversationContext,
    Message,
    TaskRun,
    TaskStatus,
    TaskStep,
    TimelineKind,
)
from desktop.workspace.safety import safe_json_dumps, sanitize_user_visible_text
from router.integration.skills import SkillResultCard


SCHEMA_VERSION = 1
FINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.PARTIAL,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


def _now() -> datetime:
    return datetime.now().astimezone()


class DesktopStateRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspace_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('USER','SYSTEM')),
                    content TEXT NOT NULL,
                    request_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_contexts (
                    conversation_id TEXT PRIMARY KEY
                        REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_audits (
                    audit_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_runs (
                    task_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    parent_task_id TEXT,
                    input_json TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(conversation_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS task_steps (
                    step_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL
                        REFERENCES task_runs(task_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS saved_task_templates (
                    template_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO workspace_meta(key,value) VALUES('schema_version',?)",
                [str(SCHEMA_VERSION)],
            )

    def create_conversation(self, title: str = "新会话") -> Conversation:
        now = _now()
        conversation_id = "conv_" + uuid4().hex[:24]
        clean_title = sanitize_user_visible_text(title).strip()[:120] or "新会话"
        context = ConversationContext(
            conversation_id=conversation_id,
            data_cutoff=now,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations
                (conversation_id,title,pinned,created_at,updated_at)
                VALUES(?,?,0,?,?)
                """,
                [conversation_id, clean_title, now.isoformat(), now.isoformat()],
            )
            connection.execute(
                """
                INSERT INTO conversation_contexts
                (conversation_id,payload_json,updated_at) VALUES(?,?,?)
                """,
                [
                    conversation_id,
                    safe_json_dumps(context.model_dump(mode="json")),
                    now.isoformat(),
                ],
            )
        return Conversation(
            conversation_id=conversation_id,
            title=clean_title,
            pinned=False,
            created_at=now,
            updated_at=now,
        )

    def list_conversations(self, search: str = "") -> list[Conversation]:
        with self._connect() as connection:
            if search.strip():
                rows = connection.execute(
                    """
                    SELECT * FROM conversations
                    WHERE title LIKE ?
                    ORDER BY pinned DESC, updated_at DESC
                    """,
                    [f"%{search.strip()}%"],
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM conversations ORDER BY pinned DESC, updated_at DESC"
                ).fetchall()
        return [self._conversation(row) for row in rows]

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        clean = sanitize_user_visible_text(title).strip()[:120]
        if not clean:
            raise ValueError("conversation title must not be empty")
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE conversations SET title=?, updated_at=?
                WHERE conversation_id=?
                """,
                [clean, _now().isoformat(), conversation_id],
            ).rowcount
        if not changed:
            raise KeyError("conversation not found")

    def pin_conversation(self, conversation_id: str, pinned: bool) -> None:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE conversations SET pinned=?, updated_at=? WHERE conversation_id=?",
                [int(pinned), _now().isoformat(), conversation_id],
            ).rowcount
        if not changed:
            raise KeyError("conversation not found")

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            return (
                connection.execute(
                    "DELETE FROM conversations WHERE conversation_id=?",
                    [conversation_id],
                ).rowcount
                > 0
            )

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        request_id: str | None = None,
    ) -> Message:
        if role not in {"USER", "SYSTEM"}:
            raise ValueError("invalid message role")
        clean = sanitize_user_visible_text(content)
        safe_json_dumps({"content": clean})
        now = _now()
        message = Message(
            message_id="msg_" + uuid4().hex[:24],
            conversation_id=conversation_id,
            role=role,
            content=clean,
            request_id=request_id,
            created_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO messages
                (message_id,conversation_id,role,content,request_id,created_at)
                VALUES(?,?,?,?,?,?)
                """,
                [
                    message.message_id,
                    conversation_id,
                    role,
                    clean,
                    request_id,
                    now.isoformat(),
                ],
            )
            connection.execute(
                "UPDATE conversations SET updated_at=? WHERE conversation_id=?",
                [now.isoformat(), conversation_id],
            )
        return message

    def messages(self, conversation_id: str) -> list[Message]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
                [conversation_id],
            ).fetchall()
        return [
            Message(
                message_id=row["message_id"],
                conversation_id=row["conversation_id"],
                role=row["role"],
                content=row["content"],
                request_id=row["request_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def context(self, conversation_id: str) -> ConversationContext:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_contexts WHERE conversation_id=?",
                [conversation_id],
            ).fetchone()
        if row is None:
            raise KeyError("conversation not found")
        return ConversationContext.model_validate(json.loads(row["payload_json"]))

    def update_context(
        self,
        conversation_id: str,
        updates: dict[str, Any],
        *,
        actor: str = "SYSTEM",
    ) -> ConversationContext:
        forbidden = {"conversation_id", "created_at"}
        if set(updates) & forbidden:
            raise ValueError("immutable context field")
        current = self.context(conversation_id)
        before = current.model_dump(mode="json")
        after = current.model_copy(
            update={**updates, "updated_at": _now()}
        )
        validated = ConversationContext.model_validate(after.model_dump())
        after_payload = validated.model_dump(mode="json")
        changed = [
            name for name in updates if before.get(name) != after_payload.get(name)
        ]
        if not changed:
            return validated
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE conversation_contexts SET payload_json=?, updated_at=?
                WHERE conversation_id=?
                """,
                [
                    safe_json_dumps(after_payload),
                    validated.updated_at.isoformat(),
                    conversation_id,
                ],
            )
            connection.execute(
                """
                INSERT INTO context_audits
                (audit_id,conversation_id,actor,changed_fields_json,
                 before_json,after_json,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                [
                    "ca_" + uuid4().hex[:24],
                    conversation_id,
                    actor,
                    safe_json_dumps(changed),
                    safe_json_dumps(before),
                    safe_json_dumps(after_payload),
                    _now().isoformat(),
                ],
            )
        return validated

    def context_audit_count(self, conversation_id: str) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM context_audits WHERE conversation_id=?",
                    [conversation_id],
                ).fetchone()[0]
            )

    def create_task(
        self,
        *,
        conversation_id: str,
        request_id: str,
        skill_id: str,
        idempotency_key: str,
        input_payload: dict[str, Any],
        status: TaskStatus = TaskStatus.PENDING,
        parent_task_id: str | None = None,
    ) -> tuple[TaskRun, bool]:
        safe_input = safe_json_dumps(input_payload)
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM task_runs
                WHERE conversation_id=? AND idempotency_key=?
                """,
                [conversation_id, idempotency_key],
            ).fetchone()
            if existing is not None:
                return self._task(existing), False
            now = _now()
            task_id = "task_" + uuid4().hex[:24]
            connection.execute(
                """
                INSERT INTO task_runs
                (task_id,conversation_id,request_id,skill_id,status,
                 idempotency_key,parent_task_id,input_json,result_json,error_code,
                 created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?,?)
                """,
                [
                    task_id,
                    conversation_id,
                    request_id,
                    skill_id,
                    status.value,
                    idempotency_key,
                    parent_task_id,
                    safe_input,
                    now.isoformat(),
                    now.isoformat(),
                ],
            )
            row = connection.execute(
                "SELECT * FROM task_runs WHERE task_id=?", [task_id]
            ).fetchone()
        return self._task(row), True

    def task(self, task_id: str) -> TaskRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_runs WHERE task_id=?", [task_id]
            ).fetchone()
        if row is None:
            raise KeyError("task not found")
        return self._task(row)

    def set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result_card: SkillResultCard | None = None,
        error_code: str | None = None,
    ) -> TaskRun:
        current = self.task(task_id)
        if current.status in FINAL_STATUSES and current.status != status:
            raise ValueError("final task status is immutable")
        result_json = (
            safe_json_dumps(result_card.model_dump(mode="json"))
            if result_card
            else None
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_runs
                SET status=?, result_json=COALESCE(?,result_json),
                    error_code=?, updated_at=?
                WHERE task_id=?
                """,
                [
                    status.value,
                    result_json,
                    error_code,
                    _now().isoformat(),
                    task_id,
                ],
            )
        return self.task(task_id)

    def request_cancel(self, task_id: str) -> TaskRun:
        current = self.task(task_id)
        if current.status in FINAL_STATUSES:
            return current
        self.set_task_status(task_id, TaskStatus.CANCEL_REQUESTED)
        return self.set_task_status(task_id, TaskStatus.CANCELLED)

    def retry_task(self, task_id: str, *, request_id: str) -> tuple[TaskRun, bool]:
        current = self.task(task_id)
        if current.status != TaskStatus.FAILED:
            raise ValueError("only failed tasks can be retried")
        return self.create_task(
            conversation_id=current.conversation_id,
            request_id=request_id,
            skill_id=current.skill_id,
            idempotency_key=f"retry:{task_id}:{request_id}",
            input_payload=current.input_payload,
            parent_task_id=task_id,
        )

    def add_step(
        self,
        task_id: str,
        kind: TimelineKind,
        label: str,
        payload: dict[str, Any] | None = None,
    ) -> TaskStep:
        clean_label = sanitize_user_visible_text(label).strip()[:200]
        safe_payload = payload or {}
        serialized = safe_json_dumps(safe_payload)
        with self._connect() as connection:
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM task_steps WHERE task_id=?",
                    [task_id],
                ).fetchone()[0]
            )
            step = TaskStep(
                step_id="step_" + uuid4().hex[:24],
                task_id=task_id,
                sequence=sequence,
                kind=kind,
                label=clean_label,
                payload=safe_payload,
                created_at=_now(),
            )
            connection.execute(
                """
                INSERT INTO task_steps
                (step_id,task_id,sequence,kind,label,payload_json,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                [
                    step.step_id,
                    task_id,
                    sequence,
                    kind.value,
                    clean_label,
                    serialized,
                    step.created_at.isoformat(),
                ],
            )
        return step

    def steps(self, task_id: str) -> list[TaskStep]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_steps WHERE task_id=? ORDER BY sequence",
                [task_id],
            ).fetchall()
        return [
            TaskStep(
                step_id=row["step_id"],
                task_id=row["task_id"],
                sequence=row["sequence"],
                kind=row["kind"],
                label=row["label"],
                payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def save_template(
        self,
        name: str,
        skill_id: str,
        payload: dict[str, Any],
        *,
        pinned: bool = False,
    ) -> str:
        serialized = safe_json_dumps(payload)
        template_id = "tpl_" + uuid4().hex[:24]
        now = _now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO saved_task_templates
                (template_id,name,skill_id,payload_json,pinned,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                [
                    template_id,
                    sanitize_user_visible_text(name)[:120],
                    skill_id,
                    serialized,
                    int(pinned),
                    now,
                    now,
                ],
            )
        return template_id

    @staticmethod
    def _conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            conversation_id=row["conversation_id"],
            title=row["title"],
            pinned=bool(row["pinned"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _task(row: sqlite3.Row) -> TaskRun:
        result = (
            SkillResultCard.model_validate(json.loads(row["result_json"]))
            if row["result_json"]
            else None
        )
        return TaskRun(
            task_id=row["task_id"],
            conversation_id=row["conversation_id"],
            request_id=row["request_id"],
            skill_id=row["skill_id"],
            status=row["status"],
            idempotency_key=row["idempotency_key"],
            parent_task_id=row["parent_task_id"],
            input_payload=json.loads(row["input_json"]),
            result_card=result,
            error_code=row["error_code"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


__all__ = ["DesktopStateRepository", "SCHEMA_VERSION"]
