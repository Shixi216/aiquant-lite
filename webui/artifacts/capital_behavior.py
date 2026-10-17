from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from webui.artifacts.paths import guarded_path
from webui.artifacts.tabular import load_csv_table
from webui.config import PROJECT_ROOT

STATES_FILE = "Capital_Behavior_State_Definitions.json"
TRANSITIONS_FILE = "Capital_Behavior_Transition_Matrix.csv"
CANDIDATES_FILE = "Capital_Behavior_Candidates.json"
MANIFEST_FILE = "Capital_Behavior_Run_Manifest.json"


def _root_file(name: str) -> Path:
    path = guarded_path(PROJECT_ROOT, name)
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def load_states() -> Any:
    return json.loads(_root_file(STATES_FILE).read_text(encoding="utf-8"))


def load_transitions() -> dict[str, Any]:
    return load_csv_table(_root_file(TRANSITIONS_FILE))


def load_candidates() -> Any:
    return json.loads(_root_file(CANDIDATES_FILE).read_text(encoding="utf-8"))


def load_manifest() -> Any:
    return json.loads(_root_file(MANIFEST_FILE).read_text(encoding="utf-8"))
