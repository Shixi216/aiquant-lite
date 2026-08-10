from __future__ import annotations

from collections.abc import Callable

from router.services.news_processor_handler import (
    NewsProcessorHandler,
)
from router.services.risk_controller_handler import RiskControllerHandler
from router.services.research_synthesizer_handler import (
    ResearchSynthesizerHandler,
)
from router.services.role_handler import RoleHandler


HandlerFactory = Callable[[], RoleHandler]


_HANDLER_FACTORIES: dict[str, HandlerFactory] = {
    "news_processor": NewsProcessorHandler,
    "research_synthesizer": ResearchSynthesizerHandler,
    "risk_controller": RiskControllerHandler,
}


def list_handler_roles() -> tuple[str, ...]:
    """Return roles with a generic invocation handler."""

    return tuple(sorted(_HANDLER_FACTORIES))


def get_role_handler(
    role_name: str,
) -> RoleHandler | None:
    """Create the handler registered for a role."""

    factory = _HANDLER_FACTORIES.get(role_name)

    if factory is None:
        return None

    return factory()
