from __future__ import annotations

from typing import Any

import httpx

from config.settings import settings


def router_url() -> str:
    return f"http://{settings.opc_router_host}:{settings.opc_router_port}"


def request_router(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 120.0,
    unwrap: bool = False,
) -> dict[str, Any]:
    try:
        with httpx.Client(
            base_url=router_url(),
            timeout=timeout,
            trust_env=False,
        ) as client:
            response = client.request(
                method,
                path,
                params=params,
                json=payload,
            )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            payload_body = exc.response.json()
            detail = str(
                payload_body.get("sanitized_error")
                or payload_body.get("detail")
                or ""
            )
        except Exception:
            pass
        raise RuntimeError(
            f"Router请求失败（HTTP {exc.response.status_code}）"
            + (f"：{detail}" if detail else "")
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(
            "Hermes-OPC Router不可用；主数据库访问已拒绝降级为跨进程直连。"
        ) from exc
    if not isinstance(body, dict):
        raise RuntimeError("Router返回了非对象响应")
    if unwrap:
        data = body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Router统一响应缺少data")
        return data
    return body


__all__ = ["request_router", "router_url"]
