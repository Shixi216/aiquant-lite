"""Provider quantities converted once to shares and CNY."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def _quantity(value: Any, unit: str, multipliers: Mapping[str, float]) -> float | None:
    if value is None or isinstance(value, bool) or unit not in multipliers:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number * multipliers[unit] if math.isfinite(number) else None


def daily_quantities(data: Mapping[str, Any], source_name: str) -> tuple[float | None, float | None]:
    """Read explicit units, with bounded compatibility for persisted raw bars.

    Older Tushare records may contain shares in ``volume`` while still marked
    LOTS. Their original ``vol`` remains unambiguous and must be used instead.
    """
    source = source_name.casefold()
    volume = data.get("volume")
    volume_unit = data.get("volume_unit")
    if data.get("volume_shares") is not None:
        volume, volume_unit = data["volume_shares"], "SHARES"
    elif source.startswith("tushare") and volume_unit != "SHARES" and data.get("vol") is not None:
        volume, volume_unit = data["vol"], "LOTS"
    elif volume is None and data.get("vol") is not None:
        volume = data["vol"]
    if volume_unit is None:
        volume_unit = "LOTS" if source.startswith(("tushare", "akshare / eastmoney")) else "SHARES"
    amount_unit = data.get("amount_unit")
    if amount_unit is None:
        amount_unit = "THOUSAND_CNY" if source.startswith("tushare") else "CNY"
    return (
        _quantity(volume, volume_unit, {"LOTS": 100.0, "SHARES": 1.0}),
        _quantity(data.get("amount"), amount_unit, {"CNY": 1.0, "THOUSAND_CNY": 1000.0, "TEN_THOUSAND_CNY": 10000.0}),
    )
