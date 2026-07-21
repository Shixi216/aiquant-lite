from __future__ import annotations

from collections.abc import Callable

from router.providers import (
    LongCatProvider,
    QwenProvider,
)
from router.services.model_provider import (
    RouterModelProvider,
)


ProviderFactory = Callable[
    [],
    RouterModelProvider,
]


_PROVIDER_FACTORIES: dict[
    str,
    ProviderFactory,
] = {
    "longcat": LongCatProvider,
    "qwen": QwenProvider,
}


def list_provider_names() -> tuple[str, ...]:
    """Return providers available to generic invocation."""

    return tuple(
        sorted(_PROVIDER_FACTORIES)
    )


def get_model_provider(
    provider_name: str,
) -> RouterModelProvider | None:
    """Create a provider registered for generic invocation."""

    factory = _PROVIDER_FACTORIES.get(
        provider_name
    )

    if factory is None:
        return None

    return factory()