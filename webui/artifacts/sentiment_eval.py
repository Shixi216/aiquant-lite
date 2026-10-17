from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from webui.artifacts.paths import guarded_path
from webui.artifacts.tabular import load_csv_table
from webui.config import PROJECT_ROOT

EVALUATION_ROOT = PROJECT_ROOT / "evaluation"
RUN_PATTERN = re.compile(r"^sentiment_v3_[A-Za-z0-9_-]+$")

RUN_FILES = {
    "ic-summary": "ic_summary.csv",
    "decay-curve": "decay_curve.json",
    "stratified-returns": "stratified_returns.csv",
    "metadata": "run_metadata.json",
}


def _run_dir(run: str) -> Path:
    if not RUN_PATTERN.match(run):
        raise FileNotFoundError(f"unknown sentiment run: {run}")
    path = guarded_path(EVALUATION_ROOT, run)
    if not path.is_dir():
        raise FileNotFoundError(f"no sentiment evaluation run: {run}")
    return path


def list_runs() -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    if EVALUATION_ROOT.is_dir():
        for entry in sorted(EVALUATION_ROOT.iterdir()):
            if not entry.is_dir() or not RUN_PATTERN.match(entry.name):
                continue
            metadata_path = entry / RUN_FILES["metadata"]
            metadata: dict[str, Any] = {}
            if metadata_path.is_file():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    metadata = {}
            runs.append(
                {
                    "run": entry.name,
                    "alpha_conclusion": metadata.get("alpha_conclusion"),
                    "date_from": metadata.get("date_from"),
                    "date_to": metadata.get("date_to"),
                    "date_count": metadata.get("date_count"),
                    "evaluation_row_count": metadata.get("evaluation_row_count"),
                    "algorithm_version": metadata.get("algorithm_version"),
                }
            )
    return {"runs": runs}


def load_run_file(run: str, name: str) -> dict[str, Any]:
    filename = RUN_FILES.get(name)
    if filename is None:
        raise FileNotFoundError(f"unknown sentiment file: {name}")
    path = _run_dir(run) / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    if filename.endswith(".json"):
        return json.loads(path.read_text(encoding="utf-8"))
    return load_csv_table(path)
