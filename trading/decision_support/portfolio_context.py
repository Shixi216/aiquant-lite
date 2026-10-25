from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading.decision_support.manual_position_manager import Holding


RESEARCH_DEFAULT_ACCOUNT_SOURCE = "RESEARCH_DEFAULT_ACCOUNT_CONTEXT"
HISTORICAL_SIMULATED_ACCOUNT_SOURCE = (
    "HISTORICAL_SIMULATED_ACCOUNT_CONTEXT"
)
MANUAL_USER_ACCOUNT_SOURCE = "MANUAL_USER_ACCOUNT_CONTEXT"


@dataclass(frozen=True)
class PortfolioContext:
    local_user_id: str
    total_assets: float
    available_cash: float
    holdings: tuple[Holding, ...] = field(default_factory=tuple)
    source: str = RESEARCH_DEFAULT_ACCOUNT_SOURCE
    as_of: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    is_research_assumption: bool = True

    def __post_init__(self) -> None:
        if not self.local_user_id.strip():
            raise ValueError("portfolio local_user_id is required")
        if self.total_assets <= 0:
            raise ValueError("total_assets must be positive")
        if self.available_cash < 0:
            raise ValueError("available_cash must not be negative")
        if self.available_cash > self.total_assets:
            raise ValueError("available_cash must not exceed total_assets")
        if not self.source.strip():
            raise ValueError("portfolio context source is required")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("portfolio as_of must include a timezone")
        if any(
            holding.local_user_id != self.local_user_id
            for holding in self.holdings
        ):
            raise ValueError("holding identity must match portfolio identity")
        if (
            self.source == MANUAL_USER_ACCOUNT_SOURCE
            and self.is_research_assumption
        ):
            raise ValueError("manual user account cannot be a research assumption")

    def holding_for(self, symbol: str) -> Holding | None:
        return next(
            (item for item in self.holdings if item.symbol == symbol),
            None,
        )

    @classmethod
    def research_default(
        cls,
        *,
        local_user_id: str,
        as_of: datetime,
        holdings: tuple[Holding, ...] = (),
    ) -> "PortfolioContext":
        return cls(
            local_user_id=local_user_id,
            total_assets=100_000.0,
            available_cash=100_000.0,
            holdings=holdings,
            source=RESEARCH_DEFAULT_ACCOUNT_SOURCE,
            as_of=as_of,
            is_research_assumption=True,
        )

    @classmethod
    def historical_simulated(
        cls,
        *,
        local_user_id: str,
        as_of: datetime,
        holdings: tuple[Holding, ...] = (),
    ) -> "PortfolioContext":
        return cls(
            local_user_id=local_user_id,
            total_assets=100_000.0,
            available_cash=100_000.0,
            holdings=holdings,
            source=HISTORICAL_SIMULATED_ACCOUNT_SOURCE,
            as_of=as_of,
            is_research_assumption=True,
        )

    @classmethod
    def manual_user(
        cls,
        *,
        local_user_id: str,
        total_assets: float,
        available_cash: float,
        holdings: tuple[Holding, ...],
        as_of: datetime,
    ) -> "PortfolioContext":
        return cls(
            local_user_id=local_user_id,
            total_assets=total_assets,
            available_cash=available_cash,
            holdings=holdings,
            source=MANUAL_USER_ACCOUNT_SOURCE,
            as_of=as_of,
            is_research_assumption=False,
        )


__all__ = [
    "HISTORICAL_SIMULATED_ACCOUNT_SOURCE",
    "MANUAL_USER_ACCOUNT_SOURCE",
    "PortfolioContext",
    "RESEARCH_DEFAULT_ACCOUNT_SOURCE",
]
