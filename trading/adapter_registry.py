from __future__ import annotations

from trading.broker import BrokerAdapter
from trading.schemas import BrokerCatalog


class BrokerAdapterRegistry:
    """Explicit registry; adapters cannot become active through request data."""

    def __init__(self, *, active_adapter_id: str) -> None:
        self.active_adapter_id = active_adapter_id
        self._adapters: dict[str, BrokerAdapter] = {}

    def register(self, adapter: BrokerAdapter) -> None:
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"Duplicate broker adapter: {adapter.adapter_id}")
        self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> BrokerAdapter | None:
        return self._adapters.get(adapter_id)

    def catalog(self) -> BrokerCatalog:
        if self.active_adapter_id not in self._adapters:
            raise RuntimeError("Active broker adapter is not registered")
        return BrokerCatalog(
            active_adapter_id=self.active_adapter_id,
            adapters=[
                self._adapters[key].capabilities()
                for key in sorted(self._adapters)
            ],
        )
