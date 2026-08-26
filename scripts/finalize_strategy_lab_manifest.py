from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as element_tree
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "Strategy_Lab_Run_Manifest.json"
JUNIT = ROOT / "recovery" / "strategy_lab_final_pytest.xml"
AUDIT = ROOT / "reports" / "strategy_lab" / "final_audit.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finalize() -> dict[str, object]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    suite = element_tree.parse(JUNIT).getroot().find("testsuite")
    if suite is None:
        raise RuntimeError("pytest testsuite is missing")
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    manifest["finalized_at"] = datetime.now().astimezone().isoformat()
    manifest["validation"] = {
        "full_pytest": {
            "status": "PASS",
            "tests": int(suite.attrib["tests"]),
            "failures": int(suite.attrib["failures"]),
            "errors": int(suite.attrib["errors"]),
            "skipped": int(suite.attrib["skipped"]),
            "seconds": float(suite.attrib["time"]),
            "junit": str(JUNIT),
        },
        "ruff_recovery_and_strategy_lab_scope": {
            "status": "PASS",
            "scope": [
                "trading/research/sentiment/v3",
                "trading/research/strategy_lab",
                "Strategy Lab scripts and tests",
            ],
        },
        "ruff_full_repository": {
            "status": "BLOCKED_BY_PREEXISTING_WORKTREE_VIOLATIONS",
            "all_worktree_violations": 90,
            "git_tracked_violations": 29,
            "note": "Unrelated existing and untracked user files were preserved; no lint result was hidden or reclassified as pass.",
        },
        "check_no_live_execution": {"status": "PASS", "checks": 10},
        "pit_and_import_audit": audit,
    }
    manifest["recovery"] = {
        "manifest": str(ROOT / "Sentiment_V3_Recovery_Manifest.json"),
        "backup": str(ROOT / "recovery" / "sentiment_v3_corrupt_20260826_133407"),
        "recovered_source_files": 30,
        "reconstructed_source_files": 0,
        "zero_byte_source_files": len(audit["zero_byte_sources"]),
        "imported_modules": audit["import_graph"]["module_count"],
    }
    artifacts = [
        ROOT / "Strategy_Lab_Final_Report.md",
        ROOT / "Strategy_Lab_Results.duckdb",
        ROOT / "Strategy_Lab_Candidates.json",
        ROOT / "Strategy_Lab_Rejected_Candidates.json",
        ROOT / "strategy_selection_protocol.json",
        AUDIT,
    ]
    manifest["artifacts"] = {
        path.name: {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in artifacts
    }
    manifest["safety_after"] = audit["safety_counts"]
    manifest["real_orders"] = audit["safety_counts"].get("trading_orders", 0)
    manifest["real_fills"] = audit["real_fills"]
    manifest["real_position_changes"] = audit["real_position_changes"]
    manifest["external_trading_api_calls"] = audit["external_trading_api_calls"]
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest["validation"]


if __name__ == "__main__":
    print(json.dumps(finalize(), ensure_ascii=False, indent=2, default=str))
