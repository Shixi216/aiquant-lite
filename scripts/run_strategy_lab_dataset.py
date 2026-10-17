from __future__ import annotations

import json
from dataclasses import asdict

from trading.research.strategy_lab.dataset_builder import StrategyLabDatasetBuilder


if __name__ == "__main__":
    result = StrategyLabDatasetBuilder().build()
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
