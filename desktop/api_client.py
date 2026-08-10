from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class ApiResult:
    ok: bool
    status_code: int | None
    data: dict[str, Any] | None = None
    error_code: str | None = None
    sanitized_error: str | None = None


class DesktopApiClient:
    def __init__(
        self,
        *,
        router_port: int = 8765,
        data_hub_port: int = 8766,
        timeout: float = 3.0,
    ) -> None:
        self.router_url = f"http://127.0.0.1:{router_port}"
        self.data_hub_url = f"http://127.0.0.1:{data_hub_port}"
        self.timeout = timeout

    def _get(self, base_url: str, path: str) -> ApiResult:
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.get(base_url + path)
            response.raise_for_status()
            payload = response.json()
            return ApiResult(
                ok=True,
                status_code=response.status_code,
                data=payload if isinstance(payload, dict) else {"value": payload},
            )
        except httpx.TimeoutException:
            return ApiResult(False, None, error_code="TIMEOUT", sanitized_error="request timed out")
        except httpx.NetworkError:
            return ApiResult(False, None, error_code="NETWORK_ERROR", sanitized_error="service is unreachable")
        except (httpx.HTTPStatusError, ValueError):
            return ApiResult(False, None, error_code="PROVIDER_ERROR", sanitized_error="service returned an invalid response")

    def router_health(self) -> ApiResult:
        return self._get(self.router_url, "/health")

    def data_hub_health(self) -> ApiResult:
        return self._get(self.data_hub_url, "/health")

    def system_health(self) -> ApiResult:
        return self._get(self.router_url, "/v1/integration/system/health")

    def doctor(self) -> ApiResult:
        return self._get(self.router_url, "/v1/integration/system/doctor")
