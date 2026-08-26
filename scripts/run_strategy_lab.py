from __future__ import annotations

import json

from trading.research.strategy_lab.runner import StrategyLabRunner


if __name__ == "__main__":
    print(json.dumps(StrategyLabRunner().run(), ensure_ascii=False, indent=2, default=str))
