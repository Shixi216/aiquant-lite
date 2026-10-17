from __future__ import annotations

import email.utils
import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from config.settings import Settings, settings
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.full_market_common import normalize_symbol


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SUPPORTED_ENDPOINTS = frozenset(
    {
        "stock_basic",
        "trade_cal",
        "stock_st",
        "namechange",
        "daily",
        "index_daily",
        "etf_daily",
        "adj_factor",
        "stk_mins",
        "etf_mins",
        "idx_mins",
    }
)
DAILY_ENDPOINTS = frozenset({"daily", "index_daily", "etf_daily"})
MINUTE_ENDPOINTS = frozenset({"stk_mins", "etf_mins", "idx_mins"})

# error_category per HTTP status; 405 is an endpoint configuration issue,
# never a SCHEMA_ERROR.
HTTP_STATUS_ERROR_CATEGORIES = {
    400: "REQUEST_ERROR",
    401: "AUTH_FAILED",
    403: "PERMISSION_DENIED",
    405: "HTTP_METHOD_NOT_ALLOWED",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
}


def resolve_endpoint_path(prefix: str | None, endpoint: str) -> str:
    """Resolve ``{prefix}/api/{endpoint}`` guarding the /api contract.

    Prevents posting to the root URL, dropping the ``/api`` prefix and
    duplicating it (``/api/api/...``).
    """
    normalized = (prefix or "").strip().rstrip("/")
    while normalized.endswith("/api/api"):
        normalized = normalized[: -len("/api/api")]
    if not normalized:
        return f"/api/{endpoint}"
    if not normalized.endswith("/api"):
        normalized = f"{normalized}/api"
    return f"{normalized}/{endpoint}"


# Documented deviations between the user-provided contract and observed
# live behavior. The contract itself is NEVER edited to mask a deviation;
# runtime may apply the listed workaround.
PROVIDER_CONTRACT_DEVIATIONS = {
    "trade_cal": {
        "deviation": "PROVIDER_CONTRACT_DEVIATION",
        "observed": (
            "cal_date single-day form returns success=true with 0 rows"
        ),
        "runtime_workaround": (
            "use start_date/end_date; start_date==end_date is allowed"
        ),
        "contract_definition": "unchanged",
    }
}


class JinniuError(RuntimeError):
    """Credential-safe provider error."""


class JinniuAuthenticationError(JinniuError):
    pass


class JinniuEnvironmentError(JinniuError):
    pass


class JinniuPermissionError(JinniuError):
    pass


class JinniuRateLimitError(JinniuError):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("Jinniu request was rate limited")
        self.retry_after = retry_after


class JinniuUnavailableError(JinniuError):
    pass


class JinniuNetworkError(JinniuError):
    pass


class JinniuSchemaError(JinniuError):
    pass


class JinniuRequestRejectedError(JinniuError):
    """Provider answered with success=false (credential-safe message)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"Jinniu rejected the request: {message}")
        self.message = message


class JinniuHttpStatusError(JinniuError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"Jinniu HTTP status {status_code}")
        self.status_code = status_code


class JinniuTruncatedError(JinniuError):
    def __init__(self, endpoint: str, meta: Mapping[str, Any]) -> None:
        super().__init__(f"Jinniu {endpoint} response remained truncated")
        self.endpoint = endpoint
        self.meta = dict(meta)


@dataclass(frozen=True)
class JinniuPage:
    endpoint: str
    fields: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    meta: dict[str, Any]
    fetched_at: datetime
    latency_ms: int

    @property
    def truncated(self) -> bool:
        return self.meta.get("truncated") is True


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (parsed - now.astimezone(timezone.utc)).total_seconds(),
        )


def parse_timestamp(value: Any, *, fallback_hour: int = 0) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            parsed = datetime.strptime(text, "%Y%m%d").replace(
                hour=fallback_hour
            )
        elif len(text) == 14 and text.isdigit():
            parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
        else:
            normalized = text.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                for pattern in (
                    "%Y-%m-%d %H:%M:%S",
                    "%Y%m%d %H:%M:%S",
                ):
                    try:
                        parsed = datetime.strptime(text, pattern)
                        break
                    except ValueError:
                        continue
                else:
                    return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class JinniuHttpClient:
    """Shared HTTP client used by every Jinniu capability."""

    def __init__(
        self,
        *,
        app_settings: Settings = settings,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        credential_loader: Callable[[], str | None] | None = None,
    ) -> None:
        api_key = credential_loader() if credential_loader else None
        api_key = api_key or os.environ.get("JINNIU_API_KEY")
        api_key = api_key or app_settings.jinniu_api_key
        if not api_key or not api_key.strip():
            raise JinniuEnvironmentError(
                "JINNIU_API_KEY is not configured"
            )
        self._api_key = api_key.strip()
        self._api_path = app_settings.jinniu_api_path
        self._max_retries = app_settings.jinniu_max_retries
        self._sleeper = sleeper
        self._clock = clock or (lambda: datetime.now(SHANGHAI_TZ))
        self._client = client or httpx.Client(
            base_url=app_settings.jinniu_base_url.rstrip("/"),
            timeout=app_settings.jinniu_timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": "AIQUANT-LITE/1",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JinniuHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _decode(
        endpoint: str,
        response: httpx.Response,
        fetched_at: datetime,
        latency_ms: int,
    ) -> JinniuPage:
        try:
            payload = response.json()
        except ValueError as exc:
            raise JinniuSchemaError(
                "Jinniu response was not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise JinniuSchemaError(
                "Jinniu response root must be an object"
            )
        if "success" not in payload:
            raise JinniuSchemaError(
                "Jinniu response must include success flag"
            )
        if payload.get("success") is not True:
            raise JinniuRequestRejectedError(
                str(payload.get("message") or "request rejected")
            )
        code = payload.get("code")
        if code not in (None, 0, "0", 200, "200"):
            raise JinniuError(
                f"Jinniu {endpoint} returned provider code {code}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise JinniuSchemaError(
                "Jinniu response data must be an object"
            )
        fields = data.get("fields")
        items = data.get("items")
        valid_fields = isinstance(fields, list) and all(
            isinstance(item, str) and item for item in fields
        )
        if not valid_fields:
            raise JinniuSchemaError(
                "Jinniu data.fields must be a list of names"
            )
        if len(fields) != len(set(fields)):
            raise JinniuSchemaError(
                "Jinniu data.fields contains duplicates"
            )
        if not isinstance(items, list):
            raise JinniuSchemaError("Jinniu data.items must be a list")
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, list) or len(item) != len(fields):
                raise JinniuSchemaError(
                    "Jinniu item width does not match data.fields"
                )
            rows.append(dict(zip(fields, item, strict=True)))
        raw_meta = payload.get("meta", data.get("meta", {}))
        if raw_meta is None:
            raw_meta = {}
        if not isinstance(raw_meta, dict):
            raise JinniuSchemaError("Jinniu meta must be an object")
        return JinniuPage(
            endpoint=endpoint,
            fields=tuple(fields),
            rows=tuple(rows),
            meta=dict(raw_meta),
            fetched_at=fetched_at,
            latency_ms=latency_ms,
        )

    def request(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: Iterable[str] | None = None,
    ) -> JinniuPage:
        if endpoint not in SUPPORTED_ENDPOINTS:
            raise ValueError(f"unsupported Jinniu endpoint: {endpoint}")
        body = {
            key: value
            for key, value in (params or {}).items()
            if value is not None
        }
        if fields:
            body["fields"] = ",".join(fields)
        url = resolve_endpoint_path(self._api_path, endpoint)
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._client.post(
                    url,
                    json=body,
                    headers={"X-API-Key": self._api_key},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self._max_retries:
                    raise JinniuNetworkError(
                        "Jinniu network request failed"
                    ) from exc
                self._sleeper(min(2**attempt, 8))
                continue
            fetched_at = self._clock().astimezone(SHANGHAI_TZ)
            latency_ms = round((time.perf_counter() - started) * 1000)
            if response.status_code == 401:
                raise JinniuAuthenticationError(
                    "Jinniu credential was rejected"
                )
            if response.status_code == 403:
                raise JinniuPermissionError(
                    f"Jinniu {endpoint} is not permitted"
                )
            if response.status_code == 429:
                delay = _retry_after_seconds(
                    response.headers.get("Retry-After"), fetched_at
                )
                if attempt >= self._max_retries:
                    raise JinniuRateLimitError(delay)
                self._sleeper(
                    delay if delay is not None else min(2**attempt, 8)
                )
                continue
            if response.status_code == 503:
                if attempt >= self._max_retries:
                    raise JinniuUnavailableError(
                        "Jinniu service is unavailable"
                    )
                delay = _retry_after_seconds(
                    response.headers.get("Retry-After"), fetched_at
                )
                self._sleeper(
                    delay if delay is not None else min(2**attempt, 8)
                )
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise JinniuHttpStatusError(response.status_code) from exc
            return self._decode(
                endpoint,
                response,
                fetched_at,
                latency_ms,
            )
        raise AssertionError("unreachable")


def classify_jinniu_error(exc: Exception) -> str:
    if isinstance(exc, JinniuEnvironmentError):
        return "ENV_NOT_VISIBLE_TO_CURRENT_RUNTIME"
    if isinstance(exc, JinniuAuthenticationError):
        return "AUTH_FAILED"
    if isinstance(exc, JinniuPermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, JinniuRateLimitError):
        return "RATE_LIMITED"
    if isinstance(exc, JinniuUnavailableError):
        return "SERVICE_UNAVAILABLE"
    if isinstance(exc, JinniuNetworkError):
        return "NETWORK_ERROR"
    if isinstance(exc, JinniuHttpStatusError):
        return HTTP_STATUS_ERROR_CATEGORIES.get(
            exc.status_code, "HTTP_STATUS_ERROR"
        )
    if isinstance(exc, (JinniuSchemaError, JinniuTruncatedError)):
        return "SCHEMA_ERROR"
    if isinstance(exc, JinniuRequestRejectedError):
        return "REQUEST_ERROR"
    return "UNKNOWN_ERROR"


class JinniuProvider:
    provider_name = "Jinniu"

    def __init__(
        self,
        client: JinniuHttpClient | None = None,
        *,
        app_settings: Settings = settings,
    ) -> None:
        self.client = client or JinniuHttpClient(app_settings=app_settings)
        self.schema_version = app_settings.jinniu_schema_version

    def fetch_page(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: Iterable[str] | None = None,
    ) -> JinniuPage:
        return self.client.request(endpoint, params=params, fields=fields)

    def fetch_complete(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: Iterable[str] | None = None,
        max_pages: int = 100,
    ) -> list[JinniuPage]:
        current = dict(params or {})
        pages: list[JinniuPage] = []
        seen: set[str] = set()
        for _ in range(max_pages):
            page = self.fetch_page(
                endpoint,
                params=current,
                fields=fields,
            )
            pages.append(page)
            if not page.truncated:
                return pages
            cursor = page.meta.get("next_cursor")
            offset = page.meta.get("next_offset")
            if cursor is not None:
                marker = f"cursor:{cursor}"
                if marker in seen:
                    break
                seen.add(marker)
                current["cursor"] = cursor
                continue
            if offset is not None:
                marker = f"offset:{offset}"
                if marker in seen:
                    break
                seen.add(marker)
                current["offset"] = offset
                continue
            break
        raise JinniuTruncatedError(
            endpoint,
            pages[-1].meta if pages else {},
        )

    @staticmethod
    def normalize_row(
        endpoint: str,
        row: Mapping[str, Any],
        *,
        fetched_at: datetime,
        data_cutoff: datetime,
        response_meta: Mapping[str, Any] | None = None,
        schema_version: str = "jinniu-shadow-v1",
    ) -> dict[str, Any]:
        normalized = dict(row)
        symbol_value = (
            row.get("ts_code")
            or row.get("symbol")
            or row.get("index_code")
        )
        symbol = normalize_symbol(symbol_value) if symbol_value else None
        if endpoint in DAILY_ENDPOINTS:
            volume = as_number(row.get("vol"))
            amount = as_number(row.get("amount"))
            normalized.update(
                {
                    "volume_shares": volume * 100 if volume is not None else None,
                    "amount_cny": amount * 1000 if amount is not None else None,
                    "volume_unit": "SHARES",
                    "amount_unit": "CNY",
                    "provider_volume_unit": "LOTS",
                    "provider_amount_unit": "THOUSAND_CNY",
                }
            )
        elif endpoint in MINUTE_ENDPOINTS:
            normalized.update(
                {
                    "volume_shares": as_number(
                        row.get("vol", row.get("volume"))
                    ),
                    "amount_cny": as_number(row.get("amount")),
                    "volume_unit": "SHARES",
                    "amount_unit": "CNY",
                    "provider_volume_unit": "SHARES",
                    "provider_amount_unit": "CNY",
                }
            )
        trade_value = (
            row.get("trade_time")
            or row.get("datetime")
            or row.get("trade_date")
            or row.get("cal_date")
            or row.get("start_date")
        )
        source_timestamp = parse_timestamp(
            trade_value,
            fallback_hour=15 if endpoint in DAILY_ENDPOINTS else 0,
        ) or fetched_at.astimezone(SHANGHAI_TZ)
        meta = dict(response_meta or {})
        available_time = parse_timestamp(
            row.get("available_time") or meta.get("available_time")
        )
        available_time = available_time or fetched_at.astimezone(
            SHANGHAI_TZ
        )
        fetched = fetched_at.astimezone(SHANGHAI_TZ)
        hash_payload = {
            "provider": "Jinniu",
            "endpoint": endpoint,
            "schema_version": schema_version,
            "data": normalized,
        }
        quality = (
            "TRUNCATED" if meta.get("truncated") is True else "COMPLETE"
        )
        return {
            "endpoint": endpoint,
            "symbol": symbol
            or (str(symbol_value).strip().upper() if symbol_value else None),
            "data": normalized,
            "provider": "Jinniu",
            "source_timestamp": source_timestamp,
            "available_time": available_time,
            "fetched_at": fetched,
            "data_cutoff": data_cutoff.astimezone(SHANGHAI_TZ),
            "content_hash": canonical_hash(hash_payload),
            "quality": quality,
            "schema_version": schema_version,
        }

    def fetch_normalized(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: Iterable[str] | None = None,
        data_cutoff: datetime | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = data_cutoff or datetime.now(SHANGHAI_TZ)
        cutoff = cutoff.astimezone(SHANGHAI_TZ)
        result: list[dict[str, Any]] = []
        pages = self.fetch_complete(
            endpoint,
            params=params,
            fields=fields,
        )
        for page in pages:
            result.extend(
                self.normalize_row(
                    endpoint,
                    row,
                    fetched_at=page.fetched_at,
                    data_cutoff=cutoff,
                    response_meta=page.meta,
                    schema_version=self.schema_version,
                )
                for row in page.rows
            )
        return result

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjustment_type: str = "RAW",
    ) -> list[MarketRecord]:
        if adjustment_type != "RAW":
            raise ValueError(
                "Jinniu daily is RAW-only; use adj_factor separately"
            )
        normalized_symbol = normalize_symbol(symbol)
        if normalized_symbol is None:
            raise ValueError("invalid A-share symbol")
        rows = self.fetch_normalized(
            "daily",
            params={
                "ts_code": normalized_symbol,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        records: list[MarketRecord] = []
        for item in rows:
            payload = dict(item["data"])
            payload.update(
                {
                    "adjustment_type": "RAW",
                    "provider_adjustment": "daily",
                    "volume": payload.get("volume_shares"),
                    "amount": payload.get("amount_cny"),
                }
            )
            records.append(
                MarketRecord(
                    record_id=(
                        f"jinniu_daily_{item['content_hash'][:32]}"
                    ),
                    symbol=normalized_symbol,
                    data_type=DataType.DAILY_BAR,
                    event_time=item["source_timestamp"],
                    fetched_at=item["fetched_at"],
                    source_name="Jinniu",
                    source_level=SourceLevel.STRUCTURED,
                    verified=False,
                    content_hash=item["content_hash"],
                    data=payload,
                )
            )
        return records

    def _endpoint(self, endpoint: str, **params: Any) -> list[dict[str, Any]]:
        return self.fetch_normalized(endpoint, params=params)

    def stock_basic(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("stock_basic", **params)

    def trade_cal(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("trade_cal", **params)

    def stock_st(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("stock_st", **params)

    def namechange(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("namechange", **params)

    def daily(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("daily", **params)

    def index_daily(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("index_daily", **params)

    def etf_daily(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("etf_daily", **params)

    def adj_factor(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("adj_factor", **params)

    def stk_mins(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("stk_mins", **params)

    def etf_mins(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("etf_mins", **params)

    def idx_mins(self, **params: Any) -> list[dict[str, Any]]:
        return self._endpoint("idx_mins", **params)


__all__ = [
    "DAILY_ENDPOINTS",
    "HTTP_STATUS_ERROR_CATEGORIES",
    "MINUTE_ENDPOINTS",
    "PROVIDER_CONTRACT_DEVIATIONS",
    "SUPPORTED_ENDPOINTS",
    "JinniuAuthenticationError",
    "JinniuEnvironmentError",
    "JinniuError",
    "JinniuHttpClient",
    "JinniuHttpStatusError",
    "JinniuNetworkError",
    "JinniuPage",
    "JinniuPermissionError",
    "JinniuProvider",
    "JinniuRateLimitError",
    "JinniuRequestRejectedError",
    "JinniuSchemaError",
    "JinniuTruncatedError",
    "JinniuUnavailableError",
    "as_number",
    "canonical_hash",
    "parse_timestamp",
    "classify_jinniu_error",
    "resolve_endpoint_path",
]
