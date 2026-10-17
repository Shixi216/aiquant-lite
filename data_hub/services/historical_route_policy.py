"""Historical route policy for stock daily / adj_factor / minutes.

Stage machine (one-click rollback = flip the stage back to ``shadow``):

- ``shadow``: status quo. Tushare stays the canonical primary; Jinniu
  runs as shadow-only dual-source observation.
- ``jinniu_primary``: qualified stage from the Jinniu Phase 3 route
  decision. Resolution REFUSES this stage unless the shadow store's
  ``HISTORICAL_MARKET`` route gate is validated, so flipping the stage
  without evidence fails closed.

Immutable classes that are never routed here: realtime quotes
(Tencent), financial statements / moneyflow (Tushare), announcements /
news (AKShare), index/ETF history (existing routes unchanged).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "config" / "historical_route_state.json"

STAGE_SHADOW = "shadow"
STAGE_JINNIU_PRIMARY = "jinniu_primary"
STAGES = (STAGE_SHADOW, STAGE_JINNIU_PRIMARY)
STAGE_ENV_OVERRIDE = "OPC_HISTORICAL_ROUTE_STAGE"


class RouteGateError(RuntimeError):
    """Raised when a stage is requested without its qualification evidence."""


@dataclass(frozen=True)
class RoutePlan:
    data_class: str
    primary: str
    fallback: tuple[str, ...] = ()
    validation: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "data_class": self.data_class,
            "primary": self.primary,
            "fallback": list(self.fallback),
            "validation": list(self.validation),
        }


ROUTE_TABLE: dict[str, dict[str, RoutePlan]] = {
    "stock_minutes": {
        STAGE_SHADOW: RoutePlan("stock_minutes", "Tushare", (), ("Jinniu",)),
        STAGE_JINNIU_PRIMARY: RoutePlan(
            "stock_minutes", "Jinniu", (), ("BaoStock",)
        ),
    },
    "stock_daily": {
        STAGE_SHADOW: RoutePlan(
            "stock_daily", "Tushare", (), ("BaoStock", "Jinniu")
        ),
        STAGE_JINNIU_PRIMARY: RoutePlan(
            "stock_daily", "Jinniu", ("Tushare",), ("BaoStock",)
        ),
    },
    "stock_adj_factor": {
        STAGE_SHADOW: RoutePlan(
            "stock_adj_factor", "Tushare", (), ("BaoStock", "Jinniu")
        ),
        STAGE_JINNIU_PRIMARY: RoutePlan(
            "stock_adj_factor", "Jinniu", ("Tushare",), ("BaoStock",)
        ),
    },
}

UNCHANGED_ROUTES: dict[str, str] = {
    "realtime_quote": "Tencent",
    "financial_statements": "Tushare",
    "moneyflow": "Tushare",
    "announcements_news": "AKShare",
    "index_daily": "Tushare",
    "index_minutes": "Tushare",
    "etf_daily": "Tushare",
    "etf_minutes": "Tushare",
}

POLICY_VERSION = "historical-route-v3.0.0-stage1"


def validate_stage(stage: str) -> str:
    if stage not in STAGES:
        raise ValueError(
            f"unknown stage {stage!r}; expected one of {list(STAGES)}"
        )
    return stage


def load_stage(
    *,
    state_path: Path = STATE_PATH,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the active stage; env override wins, missing file = shadow."""

    source = os.environ if environ is None else environ
    override = source.get(STAGE_ENV_OVERRIDE)
    if override:
        return validate_stage(override)
    if state_path.exists():
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        return validate_stage(str(payload.get("stage", STAGE_SHADOW)))
    return STAGE_SHADOW


def write_stage(
    stage: str,
    *,
    reason: str,
    updated_by: str,
    state_path: Path = STATE_PATH,
) -> dict:
    validate_stage(stage)
    payload = {
        "policy_version": POLICY_VERSION,
        "stage": stage,
        "reason": reason,
        "updated_by": updated_by,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=state_path.parent, delete=False
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.close()
        os.replace(handle.name, state_path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)
    return payload


def resolve_route(
    data_class: str,
    *,
    stage: str,
    gate_validated: bool = False,
) -> RoutePlan:
    """Resolve the provider plan; fails closed on unqualified primary stage."""

    fixed = UNCHANGED_ROUTES.get(data_class)
    if fixed is not None:
        raise ValueError(
            f"{data_class} has a fixed provider ({fixed}) and is not routable"
            " through the historical stage machine"
        )
    plans = ROUTE_TABLE.get(data_class)
    if plans is None:
        raise KeyError(f"no historical route defined for {data_class!r}")
    validate_stage(stage)
    if stage == STAGE_JINNIU_PRIMARY and not gate_validated:
        raise RouteGateError(
            "jinniu_primary stage requires a validated HISTORICAL_MARKET"
            " route gate in the shadow store"
        )
    return plans[stage]


def route_table(stage: str, *, gate_validated: bool = False) -> dict[str, dict]:
    validate_stage(stage)
    table: dict[str, dict] = {
        data_class: {"fixed_provider": fixed}
        for data_class, fixed in UNCHANGED_ROUTES.items()
    }
    for data_class, plans in ROUTE_TABLE.items():
        try:
            table[data_class] = resolve_route(
                data_class, stage=stage, gate_validated=gate_validated
            ).as_dict()
        except RouteGateError:
            table[data_class] = {
                "data_class": data_class,
                "error": "route gate not validated",
            }
    return table


__all__ = [
    "POLICY_VERSION",
    "ROUTE_TABLE",
    "RouteGateError",
    "RoutePlan",
    "STAGES",
    "STAGE_ENV_OVERRIDE",
    "STAGE_JINNIU_PRIMARY",
    "STAGE_SHADOW",
    "UNCHANGED_ROUTES",
    "load_stage",
    "resolve_route",
    "route_table",
    "validate_stage",
    "write_stage",
]
