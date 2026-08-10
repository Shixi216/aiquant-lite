from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trading.experiments.hashing import sanitize


def write_json_and_markdown(
    payload: dict[str, Any],
    *,
    output_prefix: Path,
) -> tuple[Path, Path]:
    sanitized = sanitize(payload)
    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# Experiment evaluation report",
        "",
        "- Research only: true",
        "- Profitability proven: false",
        f"- Point-in-time status: {sanitized.get('point_in_time_status')}",
        f"- Sample count: {sanitized.get('sample_count', 0)}",
        "",
        "## Risk flags",
        "",
    ]
    lines.extend(
        f"- {value}" for value in sanitized.get("risk_flags", [])
    )
    lines.extend(
        [
            "",
            "## Data quality",
            "",
            "```json",
            json.dumps(
                sanitized.get("data_quality_summary", {}),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "```",
            "",
            "## Full payload",
            "",
            "```json",
            json.dumps(
                sanitized,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "```",
            "",
            "> Historical association is not proof of causation or stable profitability.",
        ]
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


__all__ = ["write_json_and_markdown"]
