from __future__ import annotations

from datetime import datetime, timezone

from trading.decision_support.portfolio_context import PortfolioContext


# Compatibility name for Phase 1 callers. The formal contract is PortfolioContext.
AccountContext = PortfolioContext

_FIXED_AS_OF = datetime(1970, 1, 1, tzinfo=timezone.utc)

RESEARCH_DEFAULT_ACCOUNT_CONTEXT = PortfolioContext.research_default(
    local_user_id="research-default",
    as_of=_FIXED_AS_OF,
)

HISTORICAL_SIMULATED_ACCOUNT_CONTEXT = PortfolioContext.historical_simulated(
    local_user_id="formal-history-read-only",
    as_of=_FIXED_AS_OF,
)


__all__ = [
    "AccountContext",
    "HISTORICAL_SIMULATED_ACCOUNT_CONTEXT",
    "PortfolioContext",
    "RESEARCH_DEFAULT_ACCOUNT_CONTEXT",
]
