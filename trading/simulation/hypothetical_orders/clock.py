from __future__ import annotations

from datetime import datetime

def validate_order_clock(price_time: datetime, max_age_seconds: int) -> str | None:
    now = datetime.now().astimezone()
    normalized = price_time if price_time.tzinfo else price_time.astimezone()
    age = (now - normalized).total_seconds()
    if age < -5:
        return "Reference price timestamp is in the future"
    if age > max_age_seconds:
        return f"Reference price is stale ({age:.0f}s old)"
    return None
