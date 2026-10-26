"""任务控制中心路由（/v1/wealth-lab/v3/jobs/*）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trading.wealth_lab.jobs import OPERATIONS, manager
from trading.wealth_lab.schemas import envelope


def envelope_v3(data, **kwargs):
    return envelope(data, api_version="wealth-lab/v3", **kwargs)

router = APIRouter(prefix="/v1/wealth-lab/v3/jobs", tags=["wealth-lab-jobs"])


class JobSubmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    params: dict = Field(default_factory=dict)


class JobCancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


@router.post("")
def submit_job(body: JobSubmitBody):
    try:
        job, duplicated = manager.submit(body.operation, body.params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope_v3({
        "data_mode": "REAL",
        **job.public(),
        "duplicated": duplicated,
        "message": "任务已受理（幂等命中进行中任务）" if duplicated else "任务已受理",
        "whitelist": list(OPERATIONS),
    })


@router.get("")
def list_jobs(status: str | None = None, operation: str | None = None):
    return envelope_v3({"data_mode": "REAL", "jobs": manager.list_jobs(status, operation)})


@router.get("/{job_id}")
def get_job(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return envelope_v3(job.public())


@router.get("/{job_id}/logs")
def job_logs(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return envelope_v3({"data_mode": "REAL", "job_id": job_id, "status": job.status,
                     "logs": job.logs[-100:]})


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, body: JobCancelBody | None = None):
    job = manager.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return envelope_v3({"data_mode": "REAL", **job.public(),
                     "note": "QUEUED 立即取消；RUNNING 在检查点生效"})
