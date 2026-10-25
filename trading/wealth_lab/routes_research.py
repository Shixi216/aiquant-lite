"""v3.3 三模型受控研究路由（/v1/wealth-lab/v3/research/*）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trading.wealth_lab.research import audit
from trading.wealth_lab.research.pipeline import ResearchOrchestrator
from trading.wealth_lab.research.roles import ROLE_SPECS, experimental_ai_enabled
from trading.wealth_lab.routes_v3 import envelope_v3

router = APIRouter(prefix="/v1/wealth-lab/v3/research", tags=["wealth-lab-research"])
_orchestrator = ResearchOrchestrator()


class StableRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backtest_id: str
    confirm_paid_calls: bool = False
    allow_reuse: bool = True


class ExperimentalRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=4, max_length=500)
    confirm_paid_calls: bool = False


@router.get("/roles")
def research_roles():
    return envelope_v3({
        "data_mode": "REAL",
        "roles": [
            {"role": r, "display_model_name": s.display_model_name, "provider": s.provider,
             "actual_model_id": s.actual_model_id, "available": s.available,
             "config_source": s.config_source, "note": s.note}
            for r, s in ROLE_SPECS.items()
        ],
        "modes": {"STABLE_RESEARCH": {"enabled": True, "model_call_budget": 2},
                  "EXPERIMENTAL_AI_RESEARCH": {"enabled": experimental_ai_enabled(),
                                                "model_call_budget": 3,
                                                "flag": "WEALTH_LAB_EXPERIMENTAL_AI"}},
    })


@router.post("/runs/stable")
def run_stable(body: StableRunBody):
    if not body.confirm_paid_calls:
        raise HTTPException(status_code=422,
                            detail="stable 研究包含 2 次真实付费模型调用，需 confirm_paid_calls=true")
    try:
        result = _orchestrator.run_stable(body.backtest_id, allow_reuse=body.allow_reuse)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result.get("status") == "VETOED":
        return envelope_v3(result, status="DEGRADED",
                           warnings=["HARD_VETO_NOT_LIFTABLE_BY_AI"])
    return envelope_v3(result)


@router.post("/runs/experimental")
def start_experimental(body: ExperimentalRunBody):
    if not experimental_ai_enabled():
        raise HTTPException(status_code=403,
                            detail="EXPERIMENTAL_AI_RESEARCH 未启用（设置 WEALTH_LAB_EXPERIMENTAL_AI=1）")
    if not body.confirm_paid_calls:
        raise HTTPException(status_code=422,
                            detail="实验模式包含 1 次真实付费规划师调用（后续再 2 次），需 confirm_paid_calls=true")
    try:
        result = _orchestrator.start_experimental(body.goal)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope_v3(result, warnings=["AWAITING_USER_APPROVAL", "PLAN_NOT_AUTO_EXECUTED"])


@router.post("/runs/{research_run_id}/approve")
def approve_experimental(research_run_id: str):
    try:
        result = _orchestrator.resume_experimental(research_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result.get("status") == "VETOED":
        return envelope_v3(result, status="DEGRADED", warnings=["HARD_VETO_NOT_LIFTABLE_BY_AI"])
    return envelope_v3(result)


@router.get("/runs/{research_run_id}")
def get_research_run(research_run_id: str):
    record = audit.get_run(research_run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="research run not found")
    return envelope_v3({
        "data_mode": "REAL", **record,
        "model_calls": audit.calls_for_run(research_run_id),
    })
