from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trading.decision_support.canonical_shadow import (  # noqa: E402
    CanonicalDecisionShadowRepository,
    ShadowRecordSource,
)
from trading.decision_support.canonical_shadow_gate import (  # noqa: E402
    evaluate_phase3_gate,
)


def build_report(
    *,
    record_source: ShadowRecordSource = ShadowRecordSource.REAL,
    full_test_failures: int = 0,
    full_test_skipped: int = 0,
) -> dict[str, object]:
    stats = CanonicalDecisionShadowRepository().stats(
        record_source=record_source,
    )
    gate = evaluate_phase3_gate(
        stats,
        full_test_failures=full_test_failures,
        full_test_skipped=full_test_skipped,
    )
    return {
        **stats,
        "phase3_gate": {
            "eligible": gate.eligible,
            "reasons": list(gate.reasons),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Canonical DecisionEngine shadow report",
    )
    parser.add_argument(
        "--record-source",
        choices=[item.value for item in ShadowRecordSource],
        default=ShadowRecordSource.REAL.value,
    )
    parser.add_argument("--full-test-failures", type=int, default=0)
    parser.add_argument("--full-test-skipped", type=int, default=0)
    args = parser.parse_args(argv)
    report = build_report(
        record_source=ShadowRecordSource(args.record_source),
        full_test_failures=max(0, args.full_test_failures),
        full_test_skipped=max(0, args.full_test_skipped),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
