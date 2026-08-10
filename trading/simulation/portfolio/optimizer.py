from __future__ import annotations

from trading.schemas import OptimizationRequest, OptimizationResult


def optimize_portfolio(request: OptimizationRequest) -> OptimizationResult:
    """Bounded long-only allocation; negative scores receive no capital."""
    raw = {
        asset.symbol: max(0.0, asset.expected_score) / asset.volatility
        for asset in request.assets
    }
    total = sum(raw.values())
    if total == 0:
        return OptimizationResult(weights={symbol: 0.0 for symbol in raw}, cash_weight=1.0)

    weights = {
        symbol: min(request.max_position_weight, value / total * request.max_gross_exposure)
        for symbol, value in raw.items()
    }
    # Reallocate unused exposure iteratively without breaking per-name caps.
    for _ in range(len(weights)):
        remaining = request.max_gross_exposure - sum(weights.values())
        eligible = [symbol for symbol, weight in weights.items() if weight < request.max_position_weight]
        if remaining <= 1e-12 or not eligible:
            break
        eligible_raw = sum(raw[symbol] for symbol in eligible)
        for symbol in eligible:
            add = remaining * raw[symbol] / eligible_raw
            weights[symbol] = min(request.max_position_weight, weights[symbol] + add)
    gross = sum(weights.values())
    return OptimizationResult(
        weights={symbol: round(weight, 8) for symbol, weight in weights.items()},
        cash_weight=round(max(0.0, 1 - gross), 8),
    )
