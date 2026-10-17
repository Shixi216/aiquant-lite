from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from webui.artifacts.capital_behavior import (
    load_candidates,
    load_manifest,
    load_states,
    load_transitions,
)

router = APIRouter(prefix="/webui/capital-behavior", tags=["webui-capital-behavior"])


def _or_404(loader: Any) -> Any:
    try:
        return loader()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"artifact file missing: {exc}") from exc


@router.get("/states")
def get_states() -> Any:
    return _or_404(load_states)


@router.get("/transitions")
def get_transitions() -> dict[str, Any]:
    return _or_404(load_transitions)


@router.get("/candidates")
def get_candidates() -> Any:
    return _or_404(load_candidates)


@router.get("/manifest")
def get_manifest() -> Any:
    return _or_404(load_manifest)
