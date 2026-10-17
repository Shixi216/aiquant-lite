from __future__ import annotations

from pathlib import Path

from webui.artifacts.paths import guarded_path
from webui.artifacts.tabular import load_csv_table
from webui.config import PROJECT_ROOT

STRATEGY_LAB_ROOT = PROJECT_ROOT / "reports" / "strategy_lab"

CSV_FILES = {
    "candidates": "candidate_summary.csv",
    "walk-forward": "walk_forward_folds.csv",
    "yearly": "yearly_metrics.csv",
    "cost-sensitivity": "cost_sensitivity.csv",
    "sentiment-ablation": "sentiment_ablation.csv",
}


def _table(name: str) -> Path:
    filename = CSV_FILES.get(name)
    if filename is None:
        raise FileNotFoundError(f"unknown strategy-lab table: {name}")
    path = guarded_path(STRATEGY_LAB_ROOT, filename)
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path


def load_strategy_lab(name: str) -> dict:
    return load_csv_table(_table(name))
