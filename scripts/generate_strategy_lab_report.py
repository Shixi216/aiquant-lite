from __future__ import annotations

import json

from trading.research.strategy_lab.reporting import generate_deliverables


if __name__ == "__main__":
    print(json.dumps(generate_deliverables(), ensure_ascii=False, indent=2, default=str))
