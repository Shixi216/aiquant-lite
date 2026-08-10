from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RefreshResult:
    status: str
    provider: str
    batch_interface_used: bool
    fallback_symbol_requests: int
    old_snapshot_retained: bool
    error_code: str | None = None


class MarketRefreshController:
    def __init__(
        self,
        batch_refresh: Callable[[], dict[str, Any]],
    ) -> None:
        self.batch_refresh = batch_refresh

    def refresh(self, *, explicitly_confirmed: bool) -> RefreshResult:
        if not explicitly_confirmed:
            raise ValueError("market refresh requires explicit confirmation")
        try:
            self.batch_refresh()
            return RefreshResult(
                status="SUCCESS",
                provider="BATCH_PROVIDER",
                batch_interface_used=True,
                fallback_symbol_requests=0,
                old_snapshot_retained=True,
            )
        except Exception:
            return RefreshResult(
                status="FAILED",
                provider="BATCH_PROVIDER",
                batch_interface_used=True,
                fallback_symbol_requests=0,
                old_snapshot_retained=True,
                error_code="NETWORK_ERROR",
            )


__all__ = ["MarketRefreshController", "RefreshResult"]
