from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from webui.artifacts.paths import guarded_path
from webui.config import SIM_DAILY_ROOT

DATE_PATTERN = re.compile(r"^\d{8}$")
SYMBOL_PATTERN = re.compile(r"^\d{6}\.[A-Z]{2}$")

# Canonical intraday order (scripts/daily_sim_snapshot.py is the authority).
TIMEPOINT_ORDER = (
    "pre",
    "bid",
    "v1",
    "v2",
    "v3",
    "c10",
    "c1030",
    "am",
    "pm",
    "pm14",
    "pm1430",
    "vfinal",
    "close",
    "review",
)


class ArtifactNotFound(Exception):
    pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _date_dir(date: str) -> Path:
    if not DATE_PATTERN.match(date):
        raise ArtifactNotFound(f"invalid date: {date}")
    date_dir = guarded_path(SIM_DAILY_ROOT, date)
    if not date_dir.is_dir():
        raise ArtifactNotFound(f"no sim-daily directory for {date}")
    return date_dir


def list_dates() -> dict[str, Any]:
    dates = sorted(
        (
            entry.name
            for entry in SIM_DAILY_ROOT.iterdir()
            if entry.is_dir() and DATE_PATTERN.match(entry.name)
        ),
        reverse=True,
    )
    return {"dates": dates, "latest": dates[0] if dates else None}


def load_day_inventory(date: str) -> dict[str, Any]:
    date_dir = _date_dir(date)
    state: dict[str, Any] = {}
    state_path = date_dir / "state.json"
    if state_path.is_file():
        state = _read_json(state_path)

    timepoints: list[dict[str, Any]] = []
    for tp in TIMEPOINT_ORDER:
        snapshot_path = date_dir / f"snapshot_{tp}.json"
        if not snapshot_path.is_file():
            continue
        snapshot = _read_json(snapshot_path)
        timepoints.append(
            {
                "key": tp,
                "zh": snapshot.get("timepoint_zh"),
                "generated_at": snapshot.get("generated_at"),
                "result_count": len(snapshot.get("results") or []),
            }
        )

    return {
        "date": date,
        "frozen_date": state.get("frozen_date"),
        "frozen_at": state.get("frozen_at"),
        "pool": state.get("top20") or [],
        "timepoints": timepoints,
    }


def load_timepoint(date: str, timepoint: str) -> dict[str, Any]:
    date_dir = _date_dir(date)
    if timepoint not in TIMEPOINT_ORDER:
        raise ArtifactNotFound(f"unknown timepoint: {timepoint}")
    snapshot_path = guarded_path(date_dir, f"snapshot_{timepoint}.json")
    if not snapshot_path.is_file():
        raise ArtifactNotFound(f"no snapshot for {date}/{timepoint}")
    return _read_json(snapshot_path)


def load_symbol_series(date: str, symbol: str) -> dict[str, Any]:
    date_dir = _date_dir(date)
    if not SYMBOL_PATTERN.match(symbol):
        raise ArtifactNotFound(f"invalid symbol: {symbol}")

    name: str | None = None
    series: list[dict[str, Any]] = []
    for tp in TIMEPOINT_ORDER:
        snapshot_path = date_dir / f"snapshot_{tp}.json"
        if not snapshot_path.is_file():
            continue
        snapshot = _read_json(snapshot_path)
        row = next((r for r in snapshot.get("results") or [] if r.get("symbol") == symbol), None)
        if row is None:
            continue
        name = row.get("name") or name
        perf = row.get("后续表现") or {}
        series.append(
            {
                "timepoint": tp,
                "timepoint_zh": snapshot.get("timepoint_zh"),
                "generated_at": snapshot.get("generated_at"),
                "status": row.get("status"),
                "action": row.get("当前动作"),
                "score": row.get("总评分"),
                "price": row.get("参考价格"),
                "current_price": perf.get("当前价"),
                "pct": perf.get("涨跌幅"),
                "position_advice": row.get("仓位建议"),
                "intraday_strength": row.get("盘中强度"),
                "swing_quality": row.get("波段质量"),
                "veto": row.get("VETO状态"),
                "group": row.get("观察组"),
            }
        )

    if not series:
        raise ArtifactNotFound(f"symbol {symbol} not found in {date} snapshots")
    return {"date": date, "symbol": symbol, "name": name, "series": series}
