"""Wealth Lab v3.2 任务控制中心测试（本地内存队列，无网络/无付费调用）。"""
from __future__ import annotations

import time

import pytest

from trading.wealth_lab.jobs import OPERATIONS, JobManager


@pytest.fixture()
def mgr():
    return JobManager()


def test_whitelist_rejects_unknown_operation(mgr):
    with pytest.raises(ValueError, match="whitelist"):
        mgr.submit("drop_table", {})
    with pytest.raises(ValueError, match="whitelist"):
        mgr.submit("run_script", {"cmd": "rm -rf"})


def test_ai_requires_confirm(mgr):
    with pytest.raises(ValueError, match="confirm"):
        mgr.submit("run_ai_research", {"backtest_id": "wlbt_x"})


def test_idempotent_while_running(mgr):
    job1, dup1 = mgr.submit("rebuild_pit", {"slow": False})
    assert dup1 is False
    job2, dup2 = mgr.submit("rebuild_pit", {"slow": False})
    assert dup2 is True and job2.job_id == job1.job_id
    for _ in range(40):
        if mgr.get(job1.job_id).status in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(0.5)
    assert mgr.get(job1.job_id).status == "SUCCEEDED"
    # 完成后同参数提交是新任务（幂等只覆盖进行中）
    job3, dup3 = mgr.submit("rebuild_pit", {"slow": False})
    assert dup3 is False and job3.job_id != job1.job_id
    mgr.cancel(job3.job_id)


def test_cancel_queued_job(mgr):
    # 用两个任务制造排队：第一个执行中，第二个 QUEUED 可取消
    blocker, _ = mgr.submit("rebuild_pit", {})
    queued, _ = mgr.submit("rebuild_pit", {"second": True})
    cancelled = mgr.cancel(queued.job_id)
    assert cancelled is not None and cancelled.status == "CANCELLED"
    assert mgr.cancel("wljob_nonexistent0") is None


def test_job_persistence_fields(mgr):
    job, _ = mgr.submit("rebuild_pit", {"x": 1})
    for _ in range(40):
        if mgr.get(job.job_id).status in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(0.5)
    payload = mgr.get(job.job_id).public(include_logs=True)
    for field in ("job_id", "operation", "status", "progress", "created_at",
                  "started_at", "finished_at", "logs", "records_written"):
        assert field in payload
    assert payload["status"] == "SUCCEEDED"
    assert payload["progress"] == 100
    assert any("完成" in line for line in payload["logs"])


def test_operations_whitelist_contents():
    assert set(OPERATIONS) == {
        "update_fund_basic", "update_market_data", "update_adj_factors",
        "run_quality_check", "rebuild_pit", "run_backtest",
        "run_ai_research", "generate_report",
    }
