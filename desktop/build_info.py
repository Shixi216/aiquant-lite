from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from config.version import PROJECT_VERSION


DEFAULT_BUILD_INFO: dict[str, Any] = {
    "application_version": PROJECT_VERSION,
    "build_time": None,
    "git_head": None,
    "working_tree_dirty": None,
    "migration_version": "0111",
    "router_api_version": PROJECT_VERSION,
    "scanner_version": "v1",
    "orchestration_version": "v1",
    "experiment_version": "v1",
    "desktop_version": "12D",
    "build_channel": "development",
}


def build_manifest_path() -> Path:
    if bool(getattr(sys, "frozen", False)):
        root = Path(sys.executable).resolve().parent
        direct = root / "build-manifest.json"
        return direct if direct.is_file() else root / "_internal" / "build-manifest.json"
    return Path(__file__).resolve().parents[1] / "build" / "manifests" / "build-manifest.json"


def load_build_info() -> dict[str, Any]:
    payload = dict(DEFAULT_BUILD_INFO)
    path = build_manifest_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return payload
    if isinstance(loaded, dict):
        payload.update(
            {
                key: value
                for key, value in loaded.items()
                if key in DEFAULT_BUILD_INFO
            }
        )
    return payload


__all__ = ["DEFAULT_BUILD_INFO", "build_manifest_path", "load_build_info"]
