"""Wealth Lab 研究任务控制中心（v3.2 P1）。

安全规则（强制）：
- 白名单操作（8 种），不接受任意 SQL/脚本/路径；
- 单 worker 串行队列：复用 DuckDB 单写者架构，任务不并发抢写；
- 幂等：同 operation+params 的 QUEUED/RUNNING 任务重复提交返回原任务；
- 付费 AI 调用必须显式 confirm=true；
- Token 仅存后端本地环境变量；日志不记录 Token；
- 取消：QUEUED 立即；RUNNING 在资产级检查点协作取消。

状态机：QUEUED → RUNNING → SUCCEEDED | FAILED；CANCELLED（QUEUED/RUNNING）。
"""
from __future__ import annotations

import hashlib
import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import duckdb

from trading.wealth_lab.config import WEALTH_LAB_DB_PATH

_TZ = timezone(timedelta(hours=8))

JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS wealth_jobs (
    job_id VARCHAR PRIMARY KEY,
    operation VARCHAR NOT NULL,
    request_id VARCHAR,
    params_json VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    progress INTEGER DEFAULT 0,
    source VARCHAR,
    asset_scope VARCHAR,
    records_written BIGINT DEFAULT 0,
    error VARCHAR,
    result_ref VARCHAR,
    created_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    dedup_key VARCHAR
);
"""

OPERATIONS = (
    "update_fund_basic",
    "update_market_data",
    "update_adj_factors",
    "run_quality_check",
    "rebuild_pit",
    "run_backtest",
    "run_ai_research",
    "generate_report",
    # ---- Wealth Sentiment v1（独立情绪研究模块，白名单受控）----
    "refresh_wealth_news",
    "analyze_wealth_sentiment",
    "rebuild_wealth_sentiment_factor",
)


@dataclass
class Job:
    job_id: str
    operation: str
    request_id: str | None
    params: dict
    status: str = "QUEUED"
    progress: int = 0
    source: str | None = None
    asset_scope: list[str] = field(default_factory=list)
    records_written: int = 0
    error: str | None = None
    result_ref: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(_TZ))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    logs: list[str] = field(default_factory=list)
    cancel_requested: bool = False

    def public(self, include_logs: bool = False) -> dict:
        payload = {
            "job_id": self.job_id, "operation": self.operation,
            "request_id": self.request_id, "status": self.status,
            "progress": self.progress, "source": self.source,
            "asset_scope": self.asset_scope,
            "records_written": self.records_written,
            "error": self.error, "result": self.result_ref or None,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
        if include_logs:
            payload["logs"] = list(self.logs[-100:])
        return payload


class JobManager:
    """受控任务中心：本地单用户版（后端权限检查内建，不依赖前端隐藏按钮）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._queue: list[str] = []
        self._worker = threading.Thread(target=self._run_loop, daemon=True, name="wealth-jobs")
        self._worker.start()

    # ---- 提交与查询 ----
    def submit(self, operation: str, params: dict, request_id: str | None = None) -> tuple[Job, bool]:
        if operation not in OPERATIONS:
            raise ValueError(f"operation not in whitelist: {OPERATIONS}")
        if operation in ("run_ai_research", "analyze_wealth_sentiment") and not params.get("confirm"):
            raise ValueError(f"{operation} 涉及真实付费模型调用，需 params.confirm=true")
        dedup = hashlib.sha256(
            json.dumps({"op": operation, "params": params}, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]
        with self._lock:
            for job in self._jobs.values():
                if (job.status in ("QUEUED", "RUNNING")
                        and self._dedup_of(job) == dedup):
                    return job, True  # 幂等：返回进行中的同参数任务
        job = Job(
            job_id="wljob_" + uuid.uuid4().hex[:12],
            operation=operation, request_id=request_id,
            params=params, created_at=datetime.now(_TZ),
        )
        job.logs.append(f"[{job.created_at:%H:%M:%S}] 任务已受理（QUEUED）")
        with self._lock:
            self._jobs[job.job_id] = job
            self._queue.append(job.job_id)
        self._persist(job)
        return job, False

    def _dedup_of(self, job: Job) -> str:
        return hashlib.sha256(
            json.dumps({"op": job.operation, "params": job.params}, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, status: str | None = None, operation: str | None = None) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        out = []
        for job in sorted(jobs, key=lambda j: j.created_at, reverse=True):
            if status and job.status != status:
                continue
            if operation and job.operation != operation:
                continue
            out.append(job.public())
        return out

    def cancel(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "QUEUED":
                job.status = "CANCELLED"
                job.finished_at = datetime.now(_TZ)
                job.logs.append("已取消（QUEUED）")
                self._persist(job)
                return job
            if job.status == "RUNNING":
                job.cancel_requested = True
                job.logs.append("收到取消请求（将在下一个检查点生效）")
                return job
        return job

    # ---- 执行 ----
    def _run_loop(self) -> None:
        while True:
            job_id = None
            with self._lock:
                if self._queue:
                    job_id = self._queue.pop(0)
            if job_id is None:
                threading.Event().wait(0.5)
                continue
            job = self._jobs.get(job_id)
            if job is None or job.status == "CANCELLED":
                continue
            job.status = "RUNNING"
            job.started_at = datetime.now(_TZ)
            self._persist(job)
            try:
                from trading.wealth_lab.job_handlers import execute
                execute(self, job)
                with self._lock:
                    if job.cancel_requested:
                        job.status = "CANCELLED"
                        job.logs.append("已取消（执行中检查点）")
                    else:
                        job.status = "SUCCEEDED"
                        job.progress = 100
                    job.finished_at = datetime.now(_TZ)
            except Exception as exc:  # noqa: BLE001 - 任务级隔离
                job.status = "FAILED"
                job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                job.logs.append(f"失败：{job.error}")
                job.logs.append(traceback.format_exc()[-800:])
                job.finished_at = datetime.now(_TZ)
            self._persist(job)

    # ---- 任务内回调 ----
    def update(self, job: Job, progress: int | None = None, log: str | None = None,
               records: int | None = None) -> bool:
        """返回 False 表示任务已被请求取消，handler 应尽快收尾。"""
        with self._lock:
            if progress is not None:
                job.progress = max(job.progress, min(100, int(progress)))
            if log:
                job.logs.append(f"[{datetime.now(_TZ):%H:%M:%S}] {log}")
            if records:
                job.records_written += records
            return not job.cancel_requested

    def _persist(self, job: Job) -> None:
        try:
            with duckdb.connect(str(WEALTH_LAB_DB_PATH)) as con:
                con.execute(JOB_SCHEMA)
                con.execute(
                    """INSERT OR REPLACE INTO wealth_jobs VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [job.job_id, job.operation, job.request_id,
                     json.dumps(job.params, ensure_ascii=False), job.status,
                     job.progress, job.source, json.dumps(job.asset_scope, ensure_ascii=False),
                     job.records_written, job.error,
                     json.dumps(job.result_ref, ensure_ascii=False),
                     job.created_at, job.started_at, job.finished_at,
                     self._dedup_of(job)],
                )
        except Exception:  # noqa: BLE001 - 持久化失败不阻断任务
            pass


manager = JobManager()
