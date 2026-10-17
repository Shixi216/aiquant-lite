from __future__ import annotations

from pathlib import Path


def guarded_path(root: Path, *parts: str) -> Path:
    """Join parts under root and reject anything resolving outside of it."""
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts) if parts else resolved_root
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"path escapes artifact root: {resolved}")
    return resolved
