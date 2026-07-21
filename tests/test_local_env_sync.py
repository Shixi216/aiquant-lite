from pathlib import Path

import pytest

from scripts.sync_local_provider_env import EnvSyncError, parse_dotenv, patch_dotenv, sync


def test_parse_dotenv_ignores_comments_and_preserves_value_text():
    parsed = parse_dotenv("# local\nDEEPSEEK_API_KEY=test-value\nEMPTY=\n")

    assert parsed == {"DEEPSEEK_API_KEY": "test-value", "EMPTY": ""}


def test_patch_dotenv_updates_allowlist_without_copying_messaging_credentials():
    patched = patch_dotenv(
        "LOCAL_ONLY=keep\nDEEPSEEK_API_KEY=old\n",
        {"DEEPSEEK_API_KEY": "new"},
    )

    assert "LOCAL_ONLY=keep" in patched
    assert "DEEPSEEK_API_KEY=new" in patched
    assert "QQ_CLIENT_SECRET" not in patched


def test_patch_dotenv_rejects_forbidden_only_update():
    with pytest.raises(EnvSyncError, match="not allowed"):
        patch_dotenv("", {"WECOM_SECRET": "private"})


def test_sync_dry_run_never_returns_values(tmp_path: Path):
    source = tmp_path / "source.env"
    target = tmp_path / "target.env"
    source.write_text(
        "DEEPSEEK_API_KEY=private-value\nQQ_CLIENT_SECRET=private-message-value\n",
        encoding="utf-8",
    )
    target.write_text("LOCAL_ONLY=keep\n", encoding="utf-8")

    result = sync(source, target, apply=False)

    assert result["source_keys_found"] == ["DEEPSEEK_API_KEY"]
    assert "private-value" not in str(result)
    assert "private-message-value" not in str(result)
    assert target.read_text(encoding="utf-8") == "LOCAL_ONLY=keep\n"
