from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from webui.artifacts.sentiment_eval import list_runs, load_run_file
from webui.artifacts.strategy_lab import load_strategy_lab

router = APIRouter(prefix="/webui/research", tags=["webui-research"])


def _or_404(loader: Any, *args: Any) -> Any:
    try:
        return loader(*args)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/strategy-lab/{name}")
def get_strategy_lab(name: str) -> dict[str, Any]:
    return _or_404(load_strategy_lab, name)


@router.get("/sentiment-eval/runs")
def get_sentiment_runs() -> dict[str, Any]:
    return list_runs()


@router.get("/sentiment-eval/{run}/{name}")
def get_sentiment_file(run: str, name: str) -> Any:
    return _or_404(load_run_file, run, name)
