from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from config.version import PROJECT_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DESTINATION = PROJECT_ROOT / "build" / "manifests" / "build-manifest.json"


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def generate_manifest(destination: Path = DESTINATION) -> dict[str, object]:
    payload: dict[str, object] = {
        "application_version": PROJECT_VERSION,
        "build_time": datetime.now().astimezone().isoformat(),
        "git_head": _git("rev-parse", "HEAD"),
        "working_tree_dirty": bool(_git("status", "--porcelain")),
        "migration_version": "0111",
        "router_api_version": PROJECT_VERSION,
        "scanner_version": "v1",
        "orchestration_version": "v1",
        "experiment_version": "v1",
        "desktop_version": "12D",
        "build_channel": "candidate",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return payload


if __name__ == "__main__":
    print(json.dumps(generate_manifest(), ensure_ascii=False))
