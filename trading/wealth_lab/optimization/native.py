"""纯 numpy 组合优化自实现（pypfopt 未安装或与 pandas 3.0 不兼容时的兜底）。

算法归属说明：
- HRP（层次风险平价）算法思路源自 Lopez de Prado (2016)，
  《Building Diversified Portfolios that Outperform Out-of-Sample》，
  此处为独立实现（单链聚类 + 准对角化 + 递归二分方差倒数分配）。
- min_vol / max_sharpe 使用投影梯度数值优化（长仓、权重和=1、单资产上限）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _project_simplex_capped(weights: np.ndarray, cap: float) -> np.ndarray:
    """投影到 {w>=0, sum(w)=1, w<=cap}（water-filling 简化迭代）。"""
    w = np.clip(weights, 0.0, cap)
    for _ in range(60):
        total = w.sum()
        if abs(total - 1.0) < 1e-10:
            break
        deficit = 1.0 - total
        room = cap - w
        allocatable = room.sum()
        if deficit > 0 and allocatable > 0:
            w = np.minimum(cap, w + room * (deficit / allocatable))
        elif deficit < 0:
            surplus = -deficit
            above = w[w > 0]
            if above.sum() <= 0:
                break
            shrink = w * (surplus / w.sum())
            w = np.maximum(0.0, w - np.where(w > 0, shrink, 0.0))
            total = w.sum()
            if total > 0:
                w = w / total
    return np.clip(w, 0.0, cap)


def min_volatility(returns: pd.DataFrame, max_weight: float = 0.6, iterations: int = 800) -> dict[str, float]:
    cov = returns.cov().to_numpy()
    n = len(returns.columns)
    w = np.full(n, 1.0 / n)
    step = 0.02
    for _ in range(iterations):
        grad = 2.0 * cov @ w
        w = _project_simplex_capped(w - step * grad / max(np.linalg.norm(grad), 1e-12), max_weight)
    return dict(zip(returns.columns, np.round(w, 6)))


def max_sharpe(returns: pd.DataFrame, risk_free_rate: float = 0.02, max_weight: float = 0.6,
               iterations: int = 800) -> dict[str, float]:
    mu = returns.mean().to_numpy() * 252
    cov = returns.cov().to_numpy() * 252
    excess = mu - risk_free_rate
    n = len(returns.columns)
    w = np.full(n, 1.0 / n)
    step = 0.05
    for _ in range(iterations):
        port_ret = float(w @ excess)
        vol = float(np.sqrt(max(w @ cov @ w, 1e-16)))
        if vol <= 1e-8:
            break
        grad = (cov @ w * port_ret) / (vol ** 3) - excess / vol  # d(sharpe)/dw
        w = _project_simplex_capped(w + step * grad / max(np.linalg.norm(grad), 1e-12), max_weight)
    return dict(zip(returns.columns, np.round(w, 6)))


def _single_linkage_order(corr: np.ndarray) -> list[int]:
    """单链凝聚层次聚类，返回准对角化顺序（Lopez de Prado HRP 第一步）。"""
    n = corr.shape[0]
    dist = np.sqrt(np.maximum(0.0, 0.5 * (1.0 - corr)))
    clusters: list[list[int]] = [[i] for i in range(n)]
    while len(clusters) > 1:
        best = (None, float("inf"))
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = min(dist[a][b] for a in clusters[i] for b in clusters[j])
                if d < best[1]:
                    best = ((i, j), d)
        i, j = best[0]
        clusters[i] = clusters[i] + clusters[j]
        clusters.pop(j)
    return clusters[0]


def hierarchical_risk_parity(returns: pd.DataFrame, max_weight: float = 1.0) -> dict[str, float]:
    corr = returns.corr().to_numpy()
    order = _single_linkage_order(corr)
    cov = returns.cov().to_numpy()
    columns = list(returns.columns)

    def cluster_variance(indices: list[int]) -> float:
        sub = cov[np.ix_(indices, indices)]
        inv = np.linalg.pinv(sub)
        parity = inv.sum(axis=1) / inv.sum()
        return float(parity @ sub @ parity)

    weights = np.full(len(order), 1.0)
    queue: list[tuple[list[int], np.ndarray]] = [(order, weights)]
    result = np.ones(len(order))
    while queue:
        indices, w = queue.pop()
        if len(indices) == 1:
            result[indices[0]] = w[0]
            continue
        split = len(indices) // 2
        left, right = indices[:split], indices[split:]
        var_l, var_r = cluster_variance(left), cluster_variance(right)
        alpha = 1.0 - var_l / (var_l + var_r)
        w_left = w * alpha
        w_right = w * (1.0 - alpha)
        queue.append((left, w_left))
        queue.append((right, w_right))
    ordered = np.zeros(len(order))
    for pos, idx in enumerate(order):
        ordered[idx] = result[pos]
    if max_weight < 1.0:
        ordered = _project_simplex_capped(ordered, max_weight)
    return dict(zip(columns, np.round(ordered, 6)))


def estimate_stats(returns: pd.DataFrame, weights: dict[str, float], risk_free_rate: float = 0.02) -> dict:
    w = np.array([weights.get(s, 0.0) for s in returns.columns])
    mu = returns.mean().to_numpy() * 252
    cov = returns.cov().to_numpy() * 252
    expected = float(w @ mu)
    vol = float(np.sqrt(max(w @ cov @ w, 0.0)))
    sharpe = (expected - risk_free_rate) / vol if vol > 0 else 0.0
    return {
        "expected_annual_return": round(expected, 6),
        "estimated_annual_volatility": round(vol, 6),
        "estimated_sharpe": round(sharpe, 4),
    }
