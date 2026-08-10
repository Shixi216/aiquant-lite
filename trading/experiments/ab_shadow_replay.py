from __future__ import annotations

from typing import Any, Iterable
from unittest.mock import patch

import trading.experiments.formal_strategy_validation as formal_module


def replay_with_fixed_technical_scores(
    service: Any,
    inputs: Iterable[Any],
    scores: Iterable[float],
) -> list[Any]:
    input_items = list(inputs)
    score_items = [float(value) for value in scores]
    if len(input_items) != len(score_items):
        raise ValueError("shadow inputs and scores must have identical length")
    original = formal_module.technical_signal
    iterator = iter(score_items)

    def shadow_signal(bars):
        signal = original(bars)
        return signal.model_copy(update={"score": next(iterator)})

    with patch.object(formal_module, "technical_signal", shadow_signal):
        decisions = service.replay_many(input_items)
    return decisions


def merge_decisions(original: Iterable[Any], replacements: Iterable[Any]) -> list[Any]:
    values = {(item.symbol, item.signal_date): item for item in original}
    for item in replacements:
        values[(item.symbol, item.signal_date)] = item
    return sorted(values.values(), key=lambda item: (item.signal_date, item.symbol))


__all__ = ["merge_decisions", "replay_with_fixed_technical_scores"]
