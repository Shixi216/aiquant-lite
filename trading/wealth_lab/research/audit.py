"""研究运行与模型调用审计（独立表，不写股票系统记录；不记录 API Token）。"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import duckdb

from trading.wealth_lab.config import WEALTH_LAB_DB_PATH

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.Lock()

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS wealth_research_runs (
    research_run_id VARCHAR PRIMARY KEY,
    mode VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    stage VARCHAR,
    goal VARCHAR,
    plan_json VARCHAR,
    backtest_id VARCHAR,
    decision_packet_id VARCHAR,
    model_calls_used INTEGER DEFAULT 0,
    model_call_budget INTEGER,
    experiments_used INTEGER DEFAULT 0,
    error_reason VARCHAR,
    created_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS wealth_research_calls (
    call_id VARCHAR PRIMARY KEY,
    research_run_id VARCHAR NOT NULL,
    agent_role VARCHAR NOT NULL,
    display_model_name VARCHAR,
    actual_model_id VARCHAR,
    provider VARCHAR,
    prompt_version VARCHAR,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    reported_cost DOUBLE,
    status VARCHAR,
    error_reason VARCHAR,
    associated_backtest_id VARCHAR,
    evidence_ids VARCHAR
);
"""


def _connect():
    con = duckdb.connect(str(WEALTH_LAB_DB_PATH))
    con.execute(AUDIT_SCHEMA)
    return con


def create_run(research_run_id: str, mode: str, goal: str | None, budget: int) -> None:
    with _LOCK, _connect() as con:
        con.execute(
            "INSERT INTO wealth_research_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [research_run_id, mode, "PLANNING" if mode == "EXPERIMENTAL" else "BACKTESTING",
             "PLANNING" if mode == "EXPERIMENTAL" else "INIT", goal, None, None, None,
             0, budget, 0, None, datetime.now(_TZ), None],
        )


def update_run(research_run_id: str, **fields) -> None:
    allowed = {"status", "stage", "plan_json", "backtest_id", "decision_packet_id",
               "model_calls_used", "experiments_used", "error_reason", "finished_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "plan_json" in updates and isinstance(updates["plan_json"], (dict, list)):
        updates["plan_json"] = json.dumps(updates["plan_json"], ensure_ascii=False, default=str)
    if "finished_at" not in updates and updates.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
        updates["finished_at"] = datetime.now(_TZ)
    sets = ", ".join(f"{k}=?" for k in updates)
    with _LOCK, _connect() as con:
        con.execute(f"UPDATE wealth_research_runs SET {sets} WHERE research_run_id=?",
                    [*updates.values(), research_run_id])


def get_run(research_run_id: str) -> dict | None:
    with _LOCK, _connect() as con:
        row = con.execute(
            "SELECT research_run_id, mode, status, stage, goal, plan_json, backtest_id, "
            "decision_packet_id, model_calls_used, model_call_budget, experiments_used, "
            "error_reason, created_at, finished_at FROM wealth_research_runs "
            "WHERE research_run_id=?", [research_run_id]).fetchone()
    if not row:
        return None
    keys = ["research_run_id", "mode", "status", "stage", "goal", "plan_json", "backtest_id",
            "decision_packet_id", "model_calls_used", "model_call_budget", "experiments_used",
            "error_reason", "created_at", "finished_at"]
    out = dict(zip(keys, row))
    for key in ("created_at", "finished_at"):
        if out.get(key) is not None:
            out[key] = out[key].isoformat()
    return out


def log_call(research_run_id: str, agent_role: str, display_model_name: str,
             actual_model_id: str | None, provider: str, prompt_version: str,
             started_at: datetime, **meta) -> str:
    call_id = "wlrc_" + __import__("uuid").uuid4().hex[:16]
    finished = datetime.now(_TZ)
    with _LOCK, _connect() as con:
        con.execute(
            "INSERT INTO wealth_research_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [call_id, research_run_id, agent_role, display_model_name, actual_model_id,
             provider, prompt_version, started_at, finished,
             meta.get("latency_ms"), meta.get("input_tokens"), meta.get("output_tokens"),
             meta.get("reported_cost"), meta.get("status", "OK"), meta.get("error_reason"),
             meta.get("associated_backtest_id"),
             json.dumps(meta.get("evidence_ids", []), ensure_ascii=False)],
        )
    return call_id


def calls_for_run(research_run_id: str) -> list[dict]:
    with _LOCK, _connect() as con:
        rows = con.execute(
            "SELECT call_id, research_run_id, agent_role, display_model_name, actual_model_id, provider, "
            "prompt_version, started_at, finished_at, latency_ms, input_tokens, "
            "output_tokens, reported_cost, status, error_reason, associated_backtest_id, "
            "evidence_ids FROM wealth_research_calls WHERE research_run_id=? "
            "ORDER BY started_at", [research_run_id]).fetchall()
        cols = ["call_id", "research_run_id", "agent_role", "display_model_name", "actual_model_id", "provider",
                "prompt_version", "started_at", "finished_at", "latency_ms", "input_tokens",
                "output_tokens", "reported_cost", "status", "error_reason",
                "associated_backtest_id", "evidence_ids"]
    out = []
    for row in rows:
        item = dict(zip(cols, row))
        for key in ("started_at", "finished_at"):
            if item.get(key) is not None:
                item[key] = item[key].isoformat()
        try:
            item["evidence_ids"] = json.loads(item.get("evidence_ids") or "[]")
        except Exception:
            item["evidence_ids"] = []
        out.append(item)
    return out
