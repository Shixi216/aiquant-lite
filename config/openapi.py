from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def install_documented_openapi(app: FastAPI) -> None:
    """Ensure every operation has a stable description without changing routes."""

    def documented_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        for path_item in schema.get("paths", {}).values():
            for method, operation in path_item.items():
                if method.lower() not in HTTP_METHODS:
                    continue
                summary = operation.get("summary") or "Documented operation"
                operation.setdefault("description", summary)
        app.openapi_schema = schema
        return schema

    app.openapi = documented_openapi


__all__ = ["install_documented_openapi"]
