import json
from pathlib import Path

import pytest
import yaml

from scripts.configure_hermes_integration import (
    MANAGED_START,
    TOOL_NAMES,
    IntegrationError,
    build_cron_updates,
    discover_wecom_channel,
    patch_env,
    patch_mcp_config,
)


def test_patch_mcp_config_adds_exact_allowlist_and_preserves_other_settings(tmp_path: Path):
    original = "# local comment\nmodel:\n  default: test/model\n"

    patched = patch_mcp_config(original, tmp_path)
    parsed = yaml.safe_load(patched)
    server = parsed["mcp_servers"]["finance_data"]

    assert "# local comment" in patched
    assert server["tools"]["include"] == list(TOOL_NAMES)
    assert server["supports_parallel_tool_calls"] is False
    assert server["sampling"]["enabled"] is False
    assert server["env"]["PYTHONPATH"] == str(tmp_path.resolve())


def test_patch_mcp_config_is_idempotent_and_updates_managed_path(tmp_path: Path):
    first = patch_mcp_config("mcp_servers:\n", tmp_path / "first")
    second = patch_mcp_config(first, tmp_path / "second")

    assert second.count(MANAGED_START) == 1
    assert yaml.safe_load(second)["mcp_servers"]["finance_data"]["env"]["PYTHONPATH"] == str(
        (tmp_path / "second").resolve()
    )


def test_patch_mcp_config_refuses_unmanaged_entry(tmp_path: Path):
    with pytest.raises(IntegrationError, match="unmanaged"):
        patch_mcp_config("mcp_servers:\n  finance_data:\n    enabled: true\n", tmp_path)


def test_patch_env_preserves_unrelated_lines_and_does_not_duplicate_key():
    original = "# credentials\nWECOM_SECRET=hidden\nWECOM_HOME_CHANNEL=old\n"

    patched = patch_env(original, "WECOM_HOME_CHANNEL", "new")

    assert "WECOM_SECRET=hidden" in patched
    assert patched.count("WECOM_HOME_CHANNEL=") == 1
    assert "WECOM_HOME_CHANNEL=new" in patched


def test_cron_update_uses_only_finance_mcp_and_wecom(tmp_path: Path):
    updates = build_cron_updates(tmp_path)

    assert updates["deliver"] == "wecom"
    assert updates["enabled_toolsets"] == ["finance_data"]
    assert updates["workdir"] == str(tmp_path.resolve())
    assert "web_search" in updates["prompt"]
    assert "不要使用" in updates["prompt"]


def test_discover_wecom_channel_reads_platform_directory(tmp_path: Path):
    directory = tmp_path / "channel_directory.json"
    directory.write_text(
        json.dumps({"platforms": {"wecom": [{"id": "private-channel", "type": "dm"}]}}),
        encoding="utf-8",
    )

    assert discover_wecom_channel(directory) == "private-channel"
