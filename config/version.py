from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


def read_project_version(path: Path = PYPROJECT_PATH) -> str:
    """Read the single Hermes-OPC version source from pyproject.toml."""

    with path.open("rb") as pyproject_file:
        document: dict[str, Any] = tomllib.load(pyproject_file)

    version = document.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError(f"Missing [project].version in {path}")

    return version.strip()


PROJECT_VERSION = read_project_version()
ROUTER_GENERATOR_VERSION = f"router-{PROJECT_VERSION}"
