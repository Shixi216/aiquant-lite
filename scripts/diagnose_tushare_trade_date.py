from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import tushare
from dotenv import dotenv_values
from tushare.pro import client as tushare_client

from config.settings import PROJECT_ROOT, Settings, settings
from database.db import get_connection
from data_hub.providers.tushare_provider import (
    TushareProvider,
    classify_tushare_error,
)
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.history import (
    HistoryBackfillRequest,
    SelectionStrategy,
    ShardType,
)
from data_hub.services.history_backfill_service import HistoryBackfillService
from data_hub.services.history_provider_service import (
    HistoryProviderVerificationService,
)


TRADE_DATE = date(2026, 7, 28)


def token_source_flags(
    *,
    process_value: str | None,
    dotenv_value: str | None,
    settings_value: str | None,
    provider_value: str | None,
) -> dict[str, bool]:
    return {
        "process_env_token_present": bool(
            process_value and process_value.strip()
        ),
        "dotenv_token_present": bool(
            dotenv_value and dotenv_value.strip()
        ),
        "settings_token_present": bool(
            settings_value and settings_value.strip()
        ),
        "provider_token_present": bool(
            provider_value and provider_value.strip()
        ),
        "process_env_equals_dotenv": process_value == dotenv_value,
        "settings_equals_dotenv": settings_value == dotenv_value,
        "provider_equals_settings": provider_value == settings_value,
        "process_env_conflicts_dotenv": bool(
            process_value
            and dotenv_value
            and process_value != dotenv_value
        ),
    }


def _provider_token(provider: TushareProvider) -> str | None:
    value = provider._client.__dict__.get("_DataApi__token")
    return str(value) if value is not None else None


@contextmanager
def _observe_provider_codes(
    captured: dict[str, int | None],
    active_layer: list[str],
) -> Iterator[None]:
    original_post = tushare_client.requests.post

    def observing_post(*args: Any, **kwargs: Any) -> Any:
        response = original_post(*args, **kwargs)
        try:
            payload = response.json()
            code = payload.get("code")
        except Exception:
            code = None
        captured[active_layer[0]] = code
        return response

    tushare_client.requests.post = observing_post
    try:
        yield
    finally:
        tushare_client.requests.post = original_post


def _layer(
    *,
    name: str,
    rows: list[dict[str, object]] | None,
    error: Exception | None,
    provider_code: int | None,
) -> dict[str, Any]:
    if error is not None:
        error_code, reason = classify_tushare_error(error)
        request_ok = False
    elif rows:
        error_code, reason = None, None
        request_ok = True
    else:
        error_code = "EMPTY_RESULT"
        reason = "Tushare daily returned no records"
        request_ok = True
    return {
        "layer": name,
        "token_present": True,
        "provider": "TushareProvider",
        "api_name": "daily",
        "trade_date": TRADE_DATE.strftime("%Y%m%d"),
        "request_ok": request_ok,
        "returned_rows": len(rows or []),
        "provider_error_code": provider_code,
        "error_code": error_code,
        "sanitized_error": reason,
    }


def _business_counts() -> dict[str, int]:
    with get_connection() as connection:
        return {
            "data_records": int(
                connection.execute(
                    "SELECT COUNT(*) FROM data_records"
                ).fetchone()[0]
            ),
            "canonical_historical_bars": int(
                connection.execute(
                    "SELECT COUNT(*) FROM canonical_historical_bars"
                ).fetchone()[0]
            ),
        }


def run_diagnostic() -> dict[str, Any]:
    process_value = os.environ.get("TUSHARE_TOKEN")
    dotenv_raw = dotenv_values(PROJECT_ROOT / ".env").get("TUSHARE_TOKEN")
    dotenv_value = str(dotenv_raw) if dotenv_raw is not None else None
    provider = TushareProvider(token=settings.tushare_token)
    provider_value = _provider_token(provider)
    flags = token_source_flags(
        process_value=process_value,
        dotenv_value=dotenv_value,
        settings_value=settings.tushare_token,
        provider_value=provider_value,
    )
    repository = HistoryRepository()
    before = _business_counts()
    service = HistoryBackfillService(
        repository=repository,
        providers={"TUSHARE": provider},
        app_settings=settings,
    )
    request = HistoryBackfillRequest(
        dry_run=True,
        shard_type=ShardType.TRADE_DATE_SHARD,
        provider="TUSHARE",
        fallback_providers=[],
        trade_dates=[TRADE_DATE],
        target_trading_days=1,
        batch_size=1,
        concurrency=1,
        request_budget=1,
        max_retries=0,
        data_cutoff=datetime.now().astimezone(),
        selection_strategy=SelectionStrategy.TRADE_DATE,
        minimum_free_bytes=0,
    )
    layers: list[dict[str, Any]] = []
    captured: dict[str, int | None] = {}
    active_layer = [""]
    with _observe_provider_codes(captured, active_layer):
        active_layer[0] = "direct_provider"
        direct_rows: list[dict[str, object]] | None = None
        direct_error: Exception | None = None
        try:
            direct_rows = provider.get_daily_by_trade_date(TRADE_DATE)
        except Exception as exc:
            direct_error = exc
        layers.append(
            _layer(
                name=active_layer[0],
                rows=direct_rows,
                error=direct_error,
                provider_code=captured.get(active_layer[0]),
            )
        )

        active_layer[0] = "history_provider_service"
        verification = HistoryProviderVerificationService(
            repository=repository,
            app_settings=settings,
        ).verify_tushare_trade_date(
            trade_date=TRADE_DATE,
            provider=provider,
        )
        layers.append(
            {
                "layer": active_layer[0],
                "token_present": flags["provider_token_present"],
                "provider": "TushareProvider",
                "api_name": "daily",
                "trade_date": TRADE_DATE.strftime("%Y%m%d"),
                "request_ok": verification.status
                in {"AVAILABLE", "EMPTY_RESULT"},
                "returned_rows": verification.record_count,
                "provider_error_code": captured.get(active_layer[0]),
                "error_code": (
                    None
                    if verification.status == "AVAILABLE"
                    else verification.status
                ),
                "sanitized_error": verification.failure_reason,
            }
        )

        active_layer[0] = "history_backfill_dry_run"
        planned = service.run(request)
        layers.append(
            {
                "layer": active_layer[0],
                "token_present": flags["provider_token_present"],
                "provider": "TushareProvider",
                "api_name": "daily",
                "trade_date": TRADE_DATE.strftime("%Y%m%d"),
                "request_ok": planned.mode == "DRY_RUN",
                "returned_rows": 0,
                "provider_error_code": None,
                "error_code": None,
                "sanitized_error": None,
            }
        )

        active_layer[0] = "trade_date_provider_execution"
        fetched = service._trade_date_service()._fetch_date(
            run_id="diagnostic_no_persist",
            shard={
                "shard_id": "diagnostic_20260728",
                "trade_date": TRADE_DATE,
            },
            request=request,
            remaining_budget=1,
        )
        layers.append(
            _layer(
                name=active_layer[0],
                rows=fetched["rows"],
                error=fetched["error"],
                provider_code=captured.get(active_layer[0]),
            )
        )
    after = _business_counts()
    timeout = provider._client.__dict__.get("_DataApi__timeout")
    return {
        "token_sources": flags,
        "configuration": {
            "dotenv_loader": "pydantic-settings",
            "load_dotenv_called": False,
            "load_dotenv_override": None,
            "process_environment_precedes_dotenv": True,
            "settings_cached_at_import": True,
            "settings_env_file_absolute": (
                Path(Settings.model_config["env_file"]).resolve()
                == (PROJECT_ROOT / ".env").resolve()
            ),
        },
        "runtime": {
            "tushare_sdk_version": getattr(
                tushare,
                "__version__",
                "unknown",
            ),
            "working_directory": str(Path.cwd().resolve()),
            "python_interpreter": sys.executable,
            "timeout_seconds": timeout,
        },
        "request_contract": {
            "provider_class": "TushareProvider",
            "provider_method": "get_daily_by_trade_date",
            "api_name": "daily",
            "sdk_entrypoint": "pro.daily",
            "sdk_delegates_to": "DataApi.query",
            "trade_date_parameter": "trade_date",
            "trade_date_value": TRADE_DATE.strftime("%Y%m%d"),
            "fields": "",
            "extra_provider_parameters": [],
            "provider_retry_wrapper": False,
            "task_retry_count": 0,
        },
        "layers": layers,
        "historical_business_rows_before": before,
        "historical_business_rows_after": after,
        "historical_business_rows_unchanged": before == after,
    }


def main() -> int:
    print(
        json.dumps(
            run_diagnostic(),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_diagnostic", "token_source_flags"]
