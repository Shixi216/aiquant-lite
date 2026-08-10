from __future__ import annotations

from datetime import datetime
from typing import Any

from data_hub.repositories.full_market import FullMarketRepository
from data_hub.schemas.full_market import ProviderCapability


class ProviderCapabilityRegistry:
    """Persist only capabilities demonstrated by a real runtime call."""

    def __init__(
        self,
        repository: FullMarketRepository | None = None,
    ) -> None:
        self.repository = repository
        self._values: list[ProviderCapability] = []

    def record(
        self,
        *,
        provider: str,
        capability: str,
        available: bool,
        verified_at: datetime,
        batch_supported: bool,
        maximum_batch_size: int | None = None,
        rate_limit: str | None = None,
        requires_permission: bool = False,
        fallback_provider: str | None = None,
        failure_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderCapability:
        value = ProviderCapability(
            provider=provider,
            capability=capability,
            available=available,
            verified_at=verified_at,
            failure_reason=failure_reason,
            batch_supported=batch_supported,
            maximum_batch_size=maximum_batch_size,
            rate_limit=rate_limit,
            requires_permission=requires_permission,
            fallback_provider=fallback_provider,
            metadata=metadata or {},
        )
        self._values.append(value)
        return value

    def values(self) -> list[ProviderCapability]:
        return list(self._values)

    def persist(self) -> None:
        if self.repository is not None:
            self.repository.save_capabilities(self._values)


__all__ = ["ProviderCapabilityRegistry"]
