from __future__ import annotations

from fastapi import APIRouter

from trading.research.overheat_shadow_reporting import load_summary


router = APIRouter(prefix="/v1/research", tags=["overheat-shadow"])


@router.get("/overheat-shadow/status")
def overheat_shadow_status() -> dict[str, object]:
    return load_summary()


__all__ = ["router"]
