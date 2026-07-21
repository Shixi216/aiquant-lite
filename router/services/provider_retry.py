from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import httpx


TRANSIENT_HTTP_STATUS_CODES = frozenset(
    {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    }
)


@dataclass(frozen=True)
class ProviderRetryPolicy:
    """Finite retry policy for transient provider failures."""

    max_attempts: int = 2
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(
                "max_attempts 必须大于等于 1"
            )

        if self.base_delay_seconds < 0:
            raise ValueError(
                "base_delay_seconds 不能小于 0"
            )

        if self.max_delay_seconds < 0:
            raise ValueError(
                "max_delay_seconds 不能小于 0"
            )

    def delay_after_failure(
        self,
        failed_attempt: int,
    ) -> float:
        exponent = max(
            failed_attempt - 1,
            0,
        )

        delay = (
            self.base_delay_seconds
            * (2**exponent)
        )

        return min(
            delay,
            self.max_delay_seconds,
        )


DEFAULT_PROVIDER_RETRY_POLICY = (
    ProviderRetryPolicy()
)


class AuditedProviderCallError(RuntimeError):
    """Physical provider call failed after audit persistence."""

    def __init__(
        self,
        *,
        call_id: str,
        original_exception: Exception,
    ) -> None:
        super().__init__(
            str(original_exception)
        )

        self.call_id = call_id
        self.original_exception = (
            original_exception
        )


class ProviderCallSequenceError(RuntimeError):
    """Provider call sequence ended in failure."""

    def __init__(
        self,
        *,
        call_ids: list[str],
        original_exception: Exception,
    ) -> None:
        super().__init__(
            str(original_exception)
        )

        self.call_ids = tuple(call_ids)
        self.original_exception = (
            original_exception
        )


def exception_chain(    exception: BaseException,
) -> Iterator[BaseException]:
    current: BaseException | None = exception
    visited: set[int] = set()

    while current is not None:
        identity = id(current)

        if identity in visited:
            return

        visited.add(identity)
        yield current

        current = (
            current.__cause__
            or current.__context__
        )


def find_httpx_error(
    exception: BaseException,
) -> httpx.HTTPError | None:
    for item in exception_chain(exception):
        if isinstance(
            item,
            httpx.HTTPError,
        ):
            return item

    return None


def is_transient_provider_error(
    exception: BaseException,
) -> bool:
    for item in exception_chain(exception):
        if isinstance(
            item,
            httpx.HTTPStatusError,
        ):
            return (
                item.response.status_code
                in TRANSIENT_HTTP_STATUS_CODES
            )

        if isinstance(
            item,
            (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.ProtocolError,
                httpx.ProxyError,
            ),
        ):
            return True

    return False


def provider_error_type(
    exception: BaseException,
) -> str:
    httpx_error = find_httpx_error(
        exception
    )

    if httpx_error is not None:
        return type(httpx_error).__name__

    return type(exception).__name__