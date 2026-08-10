from __future__ import annotations

from datetime import date

from trading.research.overheat_shadow_reporting import summarize_rows


def test_summary_waits_for_twenty_trade_days_and_compares_scopes() -> None:
    observations = []
    labels = []
    for index in range(10):
        observation_id = f"obs_{index}"
        observations.append(
            {
                "observation_id": observation_id,
                "trade_date": date(2026, 7, index + 1),
                "a_score": 0.8 - index / 100,
                "b_score": 0.8 - index / 50,
                "a_action": "buy",
                "b_action": "hold" if index == 9 else "buy",
            }
        )
        for horizon in (1, 3, 5, 10, 20):
            labels.append(
                {
                    "observation_id": observation_id,
                    "horizon": horizon,
                    "return": (index - 4) / 100,
                    "mfe": (index + 1) / 100,
                    "mae": -(10 - index) / 100,
                }
            )
    result = summarize_rows(observations, labels)
    assert result["status"] == "COLLECTING"
    assert result["a_buy_signal_count"] == 10
    assert result["b_buy_signal_count"] == 9
    assert result["action_divergence_count"] == 1
    assert result["groups"]["A"]["top_10"]["20"]["sample_count"] == 1
    assert "production" in result["drawdown_basis"]
    assert set(result["weekly"]) == {"2026-W27", "2026-W28"}
    assert "weekly" not in result["weekly"]["2026-W27"]
