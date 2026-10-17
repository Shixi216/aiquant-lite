"""Symbol normalization for the WebUI (delegates to the production helper)."""
from __future__ import annotations

from data_hub.services.full_market_common import normalize_symbol


def canonical_candidates(symbol: str) -> list[str]:
    """Exact-match candidate set: canonical form plus the bare code."""
    canonical = normalize_symbol(symbol)
    if canonical is None:
        return []
    return list(dict.fromkeys([canonical, canonical.split(".")[0]]))


__all__ = ["normalize_symbol", "canonical_candidates"]
