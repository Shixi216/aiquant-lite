from __future__ import annotations

import inspect
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from config.settings import Settings, settings
from data_hub.providers import tushare_provider as provider_module
from data_hub.providers.tushare_provider import (
    TushareProvider,
    classify_tushare_error,
)
from data_hub.schemas.history import (
    HistoryBackfillRequest,
    SelectionStrategy,
    ShardType,
)
from data_hub.services.history_backfill_service import HistoryBackfillService
from data_hub.services.history_provider_service import (
    HistoryProviderVerificationService,
)
from scripts.diagnose_tushare_trade_date import token_source_flags
from scripts.history_cli import run_command


TRADE_DATE = date(2026, 7, 28)


def _rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "trade_date": "20260728",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "pre_close": 10.0,
                "change": 0.5,
                "pct_chg": 5.0,
                "vol": 100.0,
                "amount": 200.0,
            }
        ]
    )


class CapturingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def daily(self, **kwargs: Any) -> pd.DataFrame:
        self.calls.append(kwargs)
        return _rows()


class ReadOnlyRepository:
    database_path = Path("database/hermes_opc.duckdb").resolve()

    def latest_completed_trade_date(
        self,
        data_cutoff: datetime,
    ) -> date:
        del data_cutoff
        return TRADE_DATE

    def trading_days(self, **kwargs: Any) -> list[date]:
        del kwargs
        return [TRADE_DATE]

    def active_universe_context(
        self,
        *,
        data_cutoff: datetime,
    ) -> dict[str, dict[str, Any]]:
        del data_cutoff
        return {
            "600000.SH": {
                "exchange": "SH",
                "board": "MAIN",
                "list_date": date(1999, 11, 10),
                "delist_date": None,
                "listing_status": "ACTIVE",
                "is_suspended": False,
                "universe_version": "diagnostic-v1",
            }
        }

    def database_bytes(self) -> int:
        return 0

    def successful_trade_date_shards(
        self,
        **kwargs: Any,
    ) -> dict[date, dict[str, Any]]:
        del kwargs
        return {}


def _provider() -> tuple[TushareProvider, CapturingClient]:
    provider = TushareProvider(token="unit-test-credential")
    client = CapturingClient()
    provider._client = client
    return provider, client


def _request() -> HistoryBackfillRequest:
    return HistoryBackfillRequest(
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
        data_cutoff=datetime(
            2026,
            7,
            29,
            20,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        selection_strategy=SelectionStrategy.TRADE_DATE,
        minimum_free_bytes=0,
    )


def test_01_dotenv_token_uses_unified_settings_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "TUSHARE_TOKEN=dotenv-test-credential\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    loaded = Settings(_env_file=dotenv)
    assert loaded.tushare_token == "dotenv-test-credential"


def test_02_environment_dotenv_conflict_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "TUSHARE_TOKEN=dotenv-test-credential\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TUSHARE_TOKEN", "process-test-credential")
    loaded = Settings(_env_file=dotenv)
    flags = token_source_flags(
        process_value="process-test-credential",
        dotenv_value="dotenv-test-credential",
        settings_value=loaded.tushare_token,
        provider_value=loaded.tushare_token,
    )
    assert loaded.tushare_token == "process-test-credential"
    assert flags["process_env_conflicts_dotenv"] is True
    assert flags["settings_equals_dotenv"] is False


def test_03_cli_data_hub_and_provider_share_settings_rule() -> None:
    service_default = inspect.signature(
        HistoryBackfillService
    ).parameters["app_settings"].default
    verification_default = inspect.signature(
        HistoryProviderVerificationService
    ).parameters["app_settings"].default
    assert service_default is settings
    assert verification_default is settings
    assert provider_module.settings is settings
    assert "HistoryBackfillService()" in inspect.getsource(run_command)


def test_04_trade_date_daily_receives_no_internal_parameters() -> None:
    provider, client = _provider()
    provider.get_daily_by_trade_date(TRADE_DATE)
    assert client.calls == [{"trade_date": "20260728"}]


def test_05_iso_date_is_converted_to_compact_trade_date() -> None:
    provider, client = _provider()
    provider.get_daily_by_trade_date(
        date.fromisoformat("2026-07-28")
    )
    assert client.calls[0]["trade_date"] == "20260728"


def test_06_real_permission_error_is_classified() -> None:
    code, reason = classify_tushare_error(
        Exception("permission denied for this endpoint")
    )
    assert code == "PERMISSION_DENIED"
    assert reason == "Tushare capability is not permitted"


def test_07_parameter_error_is_not_permission_denied() -> None:
    code, _ = classify_tushare_error(
        ValueError("trade_date parameter format is invalid")
    )
    assert code == "INVALID_PARAMETER"


def test_08_network_permission_text_stays_network_error() -> None:
    code, _ = classify_tushare_error(
        ConnectionError("network access permission denied by sandbox")
    )
    assert code == "NETWORK_ERROR"


def test_09_direct_and_task_execution_use_same_provider() -> None:
    provider, client = _provider()
    direct = provider.get_daily_by_trade_date(TRADE_DATE)
    service = HistoryBackfillService(
        repository=ReadOnlyRepository(),
        providers={"TUSHARE": provider},
    )
    fetched = service._trade_date_service()._fetch_date(
        run_id="test_no_persist",
        shard={
            "shard_id": "test_20260728",
            "trade_date": TRADE_DATE,
        },
        request=_request(),
        remaining_budget=1,
    )
    assert len(direct) == len(fetched["rows"]) == 1
    assert len(client.calls) == 2
    assert service.providers["TUSHARE"] is provider
    parameters = fetched["audits"][0]["request_parameters"]
    assert parameters["provider_parameters"] == {
        "trade_date": "20260728"
    }
    assert "adjustment_type" not in parameters["provider_parameters"]


def test_10_token_does_not_enter_diagnostic_payload() -> None:
    credential = "unit-test-credential"
    flags = token_source_flags(
        process_value=None,
        dotenv_value=credential,
        settings_value=credential,
        provider_value=credential,
    )
    assert credential not in json.dumps(flags)


def test_11_dry_run_and_execution_layer_do_not_persist() -> None:
    provider, client = _provider()
    repository = ReadOnlyRepository()
    service = HistoryBackfillService(
        repository=repository,
        providers={"TUSHARE": provider},
    )
    result = service.run(_request())
    fetched = service._trade_date_service()._fetch_date(
        run_id="test_no_persist",
        shard={
            "shard_id": "test_20260728",
            "trade_date": TRADE_DATE,
        },
        request=_request(),
        remaining_budget=1,
    )
    assert result.mode == "DRY_RUN"
    assert fetched["rows"] is not None
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            RuntimeError("TUSHARE_TOKEN 未配置"),
            "TOKEN_MISSING",
        ),
        (
            RuntimeError("invalid token"),
            "TOKEN_INVALID",
        ),
        (
            RuntimeError("每分钟最多访问一次，权限详情"),
            "RATE_LIMITED",
        ),
        (
            RuntimeError("unclassified provider response"),
            "PROVIDER_ERROR",
        ),
    ],
)
def test_12_error_categories_remain_distinct(
    error: Exception,
    expected: str,
) -> None:
    assert classify_tushare_error(error)[0] == expected
