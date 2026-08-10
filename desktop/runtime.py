from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from desktop.credentials import (
    PROVIDER_ENVIRONMENT_KEYS,
    CredentialStore,
    WindowsCredentialStore,
)
from desktop.paths import AppPaths
from desktop.settings import DesktopSettings


SETTINGS_FIELD_BY_PROVIDER = {
    "tushare": "tushare_token",
    "deepseek": "deepseek_api_key",
    "qwen": "dashscope_api_key",
    "longcat": "longcat_api_key",
    "mimo": "xiaomi_api_key",
}


def activate_runtime_paths(paths: AppPaths) -> None:
    """Make every in-process repository use the selected user database."""

    os.environ["HERMES_OPC_USER_DATA_DIR"] = str(paths.user_data_dir)
    os.environ["OPC_DATABASE_PATH"] = str(paths.database_path)
    module = sys.modules.get("config.settings")
    if module is not None:
        runtime_settings = getattr(module, "settings", None)
        if runtime_settings is not None:
            runtime_settings.opc_database_path = Path(paths.database_path)


def activate_configured_credentials(
    paths: AppPaths,
    *,
    store: CredentialStore | None = None,
) -> tuple[str, ...]:
    """Load configured credentials into this process without returning values."""

    desktop_settings = DesktopSettings.load(
        paths.config_dir / "desktop-settings.json"
    )
    credential_store = store or WindowsCredentialStore()
    activated: list[str] = []
    runtime_module: Any = sys.modules.get("config.settings")
    runtime_settings = (
        getattr(runtime_module, "settings", None)
        if runtime_module is not None
        else None
    )
    router_module: Any = sys.modules.get("router.config")
    router_settings = (
        getattr(router_module, "router_settings", None)
        if router_module is not None
        else None
    )
    for provider, metadata in desktop_settings.credentials.items():
        if not metadata.configured:
            continue
        variable = PROVIDER_ENVIRONMENT_KEYS.get(provider)
        if not variable:
            continue
        try:
            value = credential_store.read(metadata.credential_key)
        except OSError:
            continue
        if not value:
            continue
        os.environ[variable] = value
        settings_field = SETTINGS_FIELD_BY_PROVIDER.get(provider)
        if runtime_settings is not None and settings_field:
            setattr(runtime_settings, settings_field, value)
        if router_settings is not None and settings_field:
            router_field = {
                "tushare_token": None,
                "deepseek_api_key": "deepseek_api_key",
                "dashscope_api_key": "qwen_api_key",
                "longcat_api_key": "longcat_api_key",
                "xiaomi_api_key": "mimo_api_key",
            }.get(settings_field)
            if router_field:
                setattr(router_settings, router_field, SecretStr(value))
        activated.append(provider)
    return tuple(sorted(activated))


def prepare_runtime(paths: AppPaths | None = None) -> AppPaths:
    selected = paths or AppPaths.resolve()
    selected.ensure_user_directories()
    activate_runtime_paths(selected)
    activate_configured_credentials(selected)
    return selected


__all__ = [
    "SETTINGS_FIELD_BY_PROVIDER",
    "activate_configured_credentials",
    "activate_runtime_paths",
    "prepare_runtime",
]
