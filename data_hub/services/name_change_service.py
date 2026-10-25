from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


def _ts_code(symbol: str) -> str:
    value = symbol.strip().upper()
    if "." in value:
        return value
    if value.startswith(("5", "6", "9")):
        return f"{value}.SH"
    if value.startswith(("4", "8")):
        return f"{value}.BJ"
    return f"{value}.SZ"


class NameChangeService:
    """低频曾用名查询；按股票缓存30天，失败时静默降级。"""

    def __init__(self, cache_dir: Path | None = None, ttl_days: int = 30) -> None:
        self.cache_dir = cache_dir or Path("cache") / "namechange"
        self.ttl = timedelta(days=ttl_days)

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{_ts_code(symbol).replace('.', '_')}.json"

    def get_history(
        self,
        symbol: str,
        *,
        allow_external_fetch: bool = True,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now = now or datetime.now().astimezone()
        path = self._cache_path(symbol)
        cached_rows: list[dict[str, Any]] = []
        fetched_at: datetime | None = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_rows = list(payload.get("rows") or [])
            fetched_at = datetime.fromisoformat(str(payload.get("fetched_at")))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.astimezone()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        if fetched_at is not None and now - fetched_at <= self.ttl:
            return cached_rows
        if not allow_external_fetch:
            return cached_rows
        try:
            from data_hub.services.capital_flow_enhanced import get_tushare_pro

            frame = get_tushare_pro().namechange(ts_code=_ts_code(symbol))
            if frame is None or frame.empty:
                return cached_rows
            rows = frame.fillna("").to_dict("records")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"fetched_at": now.isoformat(), "rows": rows},
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )
            return rows
        except Exception:
            return cached_rows


def render_name_change_history(
    rows: list[dict[str, Any]],
    *,
    as_of: date,
) -> str | None:
    """渲染近3年曾用名链；含ST/退市标识时追加风险提示。"""
    cutoff = as_of - timedelta(days=3 * 365)
    selected: list[tuple[str, str, str]] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        start = str(row.get("start_date") or "").replace("-", "")
        end = str(row.get("end_date") or "").replace("-", "")
        if not name:
            continue
        try:
            end_day = datetime.strptime(end, "%Y%m%d").date() if end else as_of
        except ValueError:
            end_day = as_of
        if end_day < cutoff:
            continue
        item = (name, start, end)
        if item not in selected:
            selected.append(item)
    if not selected:
        return None
    selected.sort(key=lambda item: item[1])

    def _day(value: str) -> str:
        if len(value) != 8:
            return ""
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"

    parts = []
    for name, start, end in selected:
        span = f"{_day(start)}~{_day(end) if end else '至今'}"
        parts.append(f"{name}({span})")
    warning = any("ST" in name.upper() or "退" in name for name, _, _ in selected)
    suffix = "；⚠ 曾戴帽/退市风险警示历史" if warning else ""
    return f"曾用名（近3年内）：{' → '.join(parts)}{suffix}"
