from dataclasses import dataclass
from datetime import date

import pytest

from trading.experiments.ab_shadow_replay import merge_decisions


@dataclass
class _Decision:
    symbol: str
    signal_date: date
    value: int


def test_merge_decisions_replaces_only_matching_signal_key():
    day = date(2026, 1, 1)
    original = [_Decision("A", day, 1), _Decision("B", day, 2)]
    result = merge_decisions(original, [_Decision("A", day, 3)])
    assert [(item.symbol, item.value) for item in result] == [("A", 3), ("B", 2)]


def test_shadow_replay_rejects_score_length_mismatch():
    from trading.experiments.ab_shadow_replay import replay_with_fixed_technical_scores

    with pytest.raises(ValueError, match="identical length"):
        replay_with_fixed_technical_scores(object(), [object()], [])
