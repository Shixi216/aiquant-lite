"""横截面 rank 四因子加权策略（落地模块）

把 Strategy Lab 验证的最优策略（技术30/基本30/情绪25/资金15）落地到生产决策链。

与回测（strategy_explore_v2/v3/v4 + Strategy Lab）严格一致。回测里有两套 rank 语义，
必须精确复现：

1. pandas `rank(pct=True, method="average")` → pct = average_rank / n
   用于：tech_rank / fund_rank / sent_rank / margin_rank（runner.py 的 _rank）

2. duckdb `RANK()`（NULLS LAST）→ pct = (min_rank - 1) / (n - 1)
   用于：情绪面 4 特征的横截面 rank（dataset_builder 的 SQL）

情绪面 = (机构净额rank + 龙虎榜净额rank + 换手rank + 缺口跟随rank) / 有数据因子数，
其中换手、缺口跟随为反向（高换手/缺口不跟随 = 看空）。

仅读本地快照/因子，不写库、不调交易执行。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 冻结的四维权重（Strategy Lab 细网格 + OOS 验证的最优）
FACTOR_WEIGHTS: dict[str, float] = {
    "tech": 0.30,
    "fund": 0.30,
    "sent": 0.25,
    "margin": 0.15,
}

# 龙虎榜机构席位识别关键字（与 Sentiment V3 capital_sources.py 一致）
INSTITUTION_SEAT = "机构专用"


def _valid(values: dict[str, float]) -> list[str]:
    return [k for k, v in values.items() if v is not None and np.isfinite(v)]


def pandas_pct_rank(values: dict[str, float]) -> dict[str, float]:
    """等价 pandas rank(pct=True, method='average')：pct = average_rank / n，*2-1。"""
    symbols = _valid(values)
    if not symbols:
        return {}
    series = pd.Series([float(values[k]) for k in symbols])
    ranked = series.rank(pct=True, method="average")
    return {k: float(ranked.iloc[i]) * 2.0 - 1.0 for i, k in enumerate(symbols)}


def duckdb_rank(values: dict[str, float]) -> dict[str, float]:
    """等价 duckdb RANK()（NULLS LAST）：pct = (min_rank - 1) / (n - 1)，*2-1。"""
    symbols = _valid(values)
    if not symbols:
        return {}
    series = pd.Series([float(values[k]) for k in symbols])
    ranked = series.rank(method="min")  # 1-based，并列给最小 rank
    n = len(symbols)
    out: dict[str, float] = {}
    for i, k in enumerate(symbols):
        pct = (ranked.iloc[i] - 1.0) / (n - 1.0) if n > 1 else 0.5
        out[k] = pct * 2.0 - 1.0
    return out


def gap_follow_through(open_px: float, close_px: float, prev_close: float | None) -> float | None:
    """缺口跟随 = max(0, sign(开盘缺口) * (close/open - 1))，与 Sentiment V3 一致。"""
    if open_px is None or close_px is None or prev_close in (None, 0) or open_px == 0:
        return None
    open_gap = open_px / prev_close - 1.0
    intraday = close_px / open_px - 1.0
    direction = 0 if open_gap == 0 else (1 if open_gap > 0 else -1)
    return max(0.0, direction * intraday)


def sentiment_composite(
    turnover: dict[str, float],
    gap_follow: dict[str, float],
    inst_net: dict[str, float],
    dt_net: dict[str, float],
) -> dict[str, float]:
    """情绪面 4 特征合成（用 duckdb RANK 语义），返回 sentiment_available_score 等价值。

    与回测 dataset_builder 的 SQL 严格一致：
    inst/dt 正向 rank；turnover/gap 先 rank 再取负（-rank，非 rank(-x)）。
    """
    inst_r = duckdb_rank(inst_net)
    dt_r = duckdb_rank(dt_net)
    turn_r = {k: (-v if v is not None else None) for k, v in duckdb_rank(turnover).items()}
    gap_r = {k: (-v if v is not None else None) for k, v in duckdb_rank(gap_follow).items()}

    all_symbols = set(inst_r) | set(dt_r) | set(turn_r) | set(gap_r)
    out: dict[str, float] = {}
    for s in all_symbols:
        parts = [inst_r.get(s), dt_r.get(s), turn_r.get(s), gap_r.get(s)]
        valid = [p for p in parts if p is not None]
        out[s] = sum(valid) / len(valid) if valid else None
    return out


def weighted_composite(
    factors: dict[str, dict[str, float]],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """四维加权（available 归一化：缺失因子权重摊给有数据因子）。"""
    weights = weights or FACTOR_WEIGHTS
    names = list(weights.keys())
    all_symbols: set[str] = set()
    for name in names:
        all_symbols |= set(factors.get(name, {}).keys())
    w = np.array([weights[name] for name in names], dtype=float)
    out: dict[str, float] = {}
    for s in all_symbols:
        vals = [factors.get(name, {}).get(s) for name in names]
        valid = np.array([v is not None and np.isfinite(v) for v in vals], dtype=bool)
        w_sum = float(valid @ w)
        if w_sum <= 0:
            out[s] = None
            continue
        num = sum((v or 0.0) * w[i] for i, v in enumerate(vals))
        out[s] = num / w_sum
    return out


__all__ = [
    "FACTOR_WEIGHTS",
    "INSTITUTION_SEAT",
    "pandas_pct_rank",
    "duckdb_rank",
    "gap_follow_through",
    "sentiment_composite",
    "weighted_composite",
]
