"""后台刷新任务锁（阶段3.0 / 任务书第二十三章）

要求：
- 任务锁：防止重复运行
- 状态记录：开始/结束时间、进度、请求次数、成功/失败数量、覆盖率、重试、错误、进程ID、最近心跳、最近成功snapshot_id
- 已有刷新任务运行时，不得重复启动第二个
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

TZ = timezone(timedelta(hours=8))

# 锁文件位置
LOCK_FILE = Path(__file__).resolve().parents[2] / "cache" / "snapshot_refresh.lock"
LOCK_STALE_SECONDS = 600  # 锁超过10分钟视为失效（进程崩溃保护）


def _json_dumps(status: "RefreshRunStatus") -> str:
    import json
    return json.dumps({
        "task_id": status.task_id,
        "started_at": status.started_at.isoformat(),
        "finished_at": status.finished_at.isoformat() if status.finished_at else None,
        "progress": status.progress,
        "request_count": status.request_count,
        "success_count": status.success_count,
        "fail_count": status.fail_count,
        "coverage_ratio": status.coverage_ratio,
        "retry_count": status.retry_count,
        "error_reason": status.error_reason,
        "process_id": status.process_id,
        "last_heartbeat": status.last_heartbeat.isoformat(),
        "last_snapshot_id": status.last_snapshot_id,
        "running": status.running,
    }, ensure_ascii=False)


@dataclass
class RefreshRunStatus:
    task_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    progress: str = "启动"
    request_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    coverage_ratio: float = 0.0
    retry_count: int = 0
    error_reason: str = ""
    process_id: int = 0
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(tz=TZ))
    last_snapshot_id: str = ""
    running: bool = True


class RefreshTaskLock:
    """刷新任务锁：防止并发重复刷新"""

    def __init__(self, lock_file: Path | None = None):
        self.lock_file = lock_file or LOCK_FILE

    def acquire(self) -> Optional[str]:
        """尝试获取锁。成功返回 task_id；已有运行中任务返回 None（不重复启动）

        原子性：用独占模式创建锁文件（O_EXCL 语义），避免并发竞争。
        """
        # 快速检查：已有活跃锁则拒绝（非原子，仅快速路径）
        if self.lock_file.exists():
            status = self.read()
            if status is not None and status.running:
                # 检查是否过期（进程崩溃）
                hb = status.last_heartbeat
                try:
                    if (datetime.now(tz=TZ) - hb).total_seconds() < LOCK_STALE_SECONDS:
                        return None  # 有活跃任务，不重复启动
                except Exception:
                    pass

        # 原子获取：尝试创建锁文件（独占）
        import uuid
        task_id = f"refresh_{uuid.uuid4().hex[:12]}"
        status = RefreshRunStatus(
            task_id=task_id, started_at=datetime.now(tz=TZ),
            process_id=os.getpid(),
        )
        try:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            # O_EXCL：文件已存在则抛 FileExistsError（原子）
            fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_json_dumps(status))
            return task_id
        except FileExistsError:
            # 竞态中另一个进程先创建了锁 → 再次读取检查
            other = self.read()
            if other is not None and other.running:
                try:
                    if (datetime.now(tz=TZ) - other.last_heartbeat).total_seconds() < LOCK_STALE_SECONDS:
                        return None  # 其他进程持锁中
                except Exception:
                    pass
            # 锁已过期（崩溃残留）→ 尝试覆盖
            try:
                self.lock_file.write_text(_json_dumps(status), encoding="utf-8")
                return task_id
            except Exception:
                return None

    def _write(self, status: RefreshRunStatus) -> None:
        import json
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file.write_text(json.dumps({
            "task_id": status.task_id,
            "started_at": status.started_at.isoformat(),
            "finished_at": status.finished_at.isoformat() if status.finished_at else None,
            "progress": status.progress,
            "request_count": status.request_count,
            "success_count": status.success_count,
            "fail_count": status.fail_count,
            "coverage_ratio": status.coverage_ratio,
            "retry_count": status.retry_count,
            "error_reason": status.error_reason,
            "process_id": status.process_id,
            "last_heartbeat": status.last_heartbeat.isoformat(),
            "last_snapshot_id": status.last_snapshot_id,
            "running": status.running,
        }, ensure_ascii=False), encoding="utf-8")

    def heartbeat(self, task_id: str, **updates) -> None:
        """更新心跳和进度"""
        status = self.read()
        if status is None or status.task_id != task_id:
            return
        status.last_heartbeat = datetime.now(tz=TZ)
        for k, v in updates.items():
            if hasattr(status, k):
                setattr(status, k, v)
        self._write(status)

    def read(self) -> Optional[RefreshRunStatus]:
        if not self.lock_file.exists():
            return None
        try:
            import json
            d = json.loads(self.lock_file.read_text(encoding="utf-8"))
            return RefreshRunStatus(
                task_id=d.get("task_id", ""),
                started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else datetime.now(tz=TZ),
                finished_at=datetime.fromisoformat(d["finished_at"]) if d.get("finished_at") else None,
                progress=d.get("progress", ""),
                request_count=d.get("request_count", 0),
                success_count=d.get("success_count", 0),
                fail_count=d.get("fail_count", 0),
                coverage_ratio=d.get("coverage_ratio", 0.0),
                retry_count=d.get("retry_count", 0),
                error_reason=d.get("error_reason", ""),
                process_id=d.get("process_id", 0),
                last_heartbeat=datetime.fromisoformat(d["last_heartbeat"]) if d.get("last_heartbeat") else datetime.now(tz=TZ),
                last_snapshot_id=d.get("last_snapshot_id", ""),
                running=d.get("running", False),
            )
        except Exception:
            return None

    def release(self, task_id: str, **final_updates) -> None:
        """释放锁（标记完成）"""
        status = self.read()
        if status is None:
            return
        status.running = False
        status.finished_at = datetime.now(tz=TZ)
        for k, v in final_updates.items():
            if hasattr(status, k):
                setattr(status, k, v)
        self._write(status)
