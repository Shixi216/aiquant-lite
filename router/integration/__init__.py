"""Stage 11 local integration surface for Hermes-OPC."""

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "router":
        from router.integration.routes import router

        return router
    raise AttributeError(name)

__all__ = ["router"]
