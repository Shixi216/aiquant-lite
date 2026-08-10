from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import tushare
from pydantic import SecretStr

from desktop.api_client import DesktopApiClient
from desktop.paths import AppPaths
from config.utf8 import UTF8_ENCODING, UTF8_ERRORS, utf8_child_environment
from desktop.safety_contract import packaged_no_live_check
from desktop.service_supervisor import port_is_open
from router.integration.health import migration_checksum_state
from router.config import router_settings
from router.services.provider_registry import get_model_provider


_MODEL_CREDENTIAL_FIELDS = {
    "deepseek": ("DEEPSEEK_API_KEY", "deepseek_api_key"),
    "qwen": ("DASHSCOPE_API_KEY", "qwen_api_key"),
    "longcat": ("LONGCAT_API_KEY", "longcat_api_key"),
    "mimo": ("XIAOMI_API_KEY", "mimo_api_key"),
}


def _minimal_model_probe(provider: str, credential: str) -> bool:
    mapping = _MODEL_CREDENTIAL_FIELDS.get(provider)
    model_provider = get_model_provider(provider)
    if mapping is None or model_provider is None:
        return False
    variable, field = mapping
    previous_environment = os.environ.get(variable)
    previous_setting = getattr(router_settings, field)
    os.environ[variable] = credential
    setattr(router_settings, field, SecretStr(credential))
    try:
        response = asyncio.run(
            model_provider.invoke(
                role="provider_probe",
                prompt="Reply with OK.",
                system_prompt="Return only OK.",
                temperature=0,
                max_tokens=16,
            )
        )
        return bool(response.content.strip())
    finally:
        if previous_environment is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = previous_environment
        setattr(router_settings, field, previous_setting)


@dataclass(frozen=True, slots=True)
class SetupCheck:
    name: str
    ok: bool
    status: str
    detail: str


class SetupService:
    def __init__(
        self,
        paths: AppPaths,
        *,
        api_client: DesktopApiClient | None = None,
        model_probe: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.paths = paths
        self.api_client = api_client or DesktopApiClient()
        self.model_probe = model_probe or _minimal_model_probe

    def test_services(self) -> tuple[SetupCheck, SetupCheck]:
        router = self.api_client.router_health()
        data_hub = self.api_client.data_hub_health()
        return (
            SetupCheck(
                "router", router.ok, "HEALTHY" if router.ok else "NOT_READY", "local API"
            ),
            SetupCheck(
                "data_hub",
                data_hub.ok,
                "HEALTHY" if data_hub.ok else "NOT_READY",
                "local API",
            ),
        )

    def test_tushare(self, token: str) -> SetupCheck:
        if not token.strip():
            return SetupCheck("tushare", False, "NOT_CONFIGURED", "credential is missing")
        today = date.today()
        try:
            result = tushare.pro_api(token).trade_cal(
                exchange="SSE",
                start_date=(today - timedelta(days=7)).strftime("%Y%m%d"),
                end_date=today.strftime("%Y%m%d"),
                fields="exchange,cal_date,is_open",
            )
            rows = 0 if result is None else len(result.index)
            return SetupCheck("tushare", rows > 0, "HEALTHY", f"returned_rows={rows}")
        except Exception:
            return SetupCheck(
                "tushare", False, "PROVIDER_ERROR", "sanitized provider validation failed"
            )

    def test_model(self, provider: str, credential: str) -> SetupCheck:
        if not credential.strip():
            return SetupCheck(provider, False, "NOT_CONFIGURED", "credential is missing")
        try:
            ok = bool(self.model_probe(provider, credential))
        except Exception:
            ok = False
        return SetupCheck(
            provider,
            ok,
            "HEALTHY" if ok else "PROVIDER_ERROR",
            "sanitized minimal model validation",
        )

    def local_checks(
        self,
        *,
        database_path: Path | None = None,
    ) -> list[SetupCheck]:
        database = self.paths.validate_database(database_path)
        free = shutil.disk_usage(self.paths.user_data_dir).free
        no_live = self._no_live_check()
        migration_ok = bool(database.get("ok"))
        ports_usable = all(
            not port_is_open(port) or check.ok
            for port, check in zip(
                (8765, 8766),
                self.test_services(),
                strict=True,
            )
        )
        return [
            SetupCheck(
                "database",
                bool(database.get("ok")),
                "HEALTHY" if database.get("ok") else "FAILED",
                "read-only open",
            ),
            SetupCheck(
                "migrations",
                migration_ok,
                "HEALTHY" if migration_ok else "FAILED",
                "0100-0111 checksum",
            ),
            SetupCheck(
                "disk",
                free >= 10 * 1024**3,
                "HEALTHY" if free >= 10 * 1024**3 else "DEGRADED",
                f"free_bytes={free}",
            ),
            SetupCheck(
                "ports",
                ports_usable,
                "HEALTHY" if ports_usable else "FAILED",
                "8765/8766 are free or owned by healthy Hermes services",
            ),
            no_live,
        ]

    def _no_live_check(self) -> SetupCheck:
        if not self.paths.development:
            ok = packaged_no_live_check()
            return SetupCheck(
                "no_live",
                ok,
                "HEALTHY" if ok else "FAILED",
                "packaged no-live safety contract",
            )
        python = self.paths.application_root / ".venv" / "Scripts" / "python.exe"
        script = self.paths.application_root / "scripts" / "check_no_live_execution.py"
        if not python.is_file() or not script.is_file():
            return SetupCheck(
                "no_live", False, "NOT_READY", "development safety script unavailable"
            )
        try:
            result = subprocess.run(
                [str(python), str(script)],
                cwd=self.paths.application_root,
                capture_output=True,
                text=True,
                encoding=UTF8_ENCODING,
                errors=UTF8_ERRORS,
                env=utf8_child_environment(),
                timeout=120,
                check=False,
            )
            ok = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        return SetupCheck(
            "no_live",
            ok,
            "HEALTHY" if ok else "FAILED",
            "A-J safety boundary",
        )

    def migration_check(self, database_path: Path | None = None) -> SetupCheck:
        try:
            ok = bool(
                migration_checksum_state(database_path or self.paths.database_path)["ok"]
            )
        except Exception:
            ok = False
        return SetupCheck(
            "migrations",
            ok,
            "HEALTHY" if ok else "FAILED",
            "0100-0111 checksum",
        )
