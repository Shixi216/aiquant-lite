from __future__ import annotations

from datetime import date, timedelta

from trading.research.technical.analysis import technical_signal
from trading.schemas import Bar


def _bars(closes: list[float]) -> list[Bar]:
    start = date(2026, 6, 1)
    rows: list[Bar] = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        rows.append(
            Bar(
                trade_date=start + timedelta(days=index),
                open=previous,
                high=max(previous, close) * 1.002,
                low=min(previous, close) * 0.998,
                close=close,
                volume=1_000_000 + index * 10_000,
            )
        )
    return rows


def test_surge_then_two_limit_downs_is_flagged_as_reversal() -> None:
    base = [4.0 + index * 0.01 for index in range(54)]
    surge = [base[-1] * 1.10]
    surge.extend([surge[-1] * 1.10 for _ in range(3)])
    collapse = [surge[-1] * 0.9005, surge[-1] * 0.9005 * 0.8998]
    signal = technical_signal(_bars(base + surge + collapse))
    assert signal.score < 0
    assert any("短期均线反压" in item for item in signal.evidence)
    assert any("顶部反转特征" in item for item in signal.evidence)


def test_healthy_gradual_uptrend_remains_positive() -> None:
    signal = technical_signal(_bars([5.0 + index * 0.025 for index in range(60)]))
    assert signal.score > 0
    assert not any("顶部反转特征" in item for item in signal.evidence)


def test_persistent_downtrend_is_negative() -> None:
    signal = technical_signal(_bars([8.0 - index * 0.035 for index in range(60)]))
    assert signal.score < 0


def test_macd_summary_uses_dif_label_not_proxy() -> None:
    signal = technical_signal(_bars([5.0 + index * 0.01 for index in range(60)]))
    assert "DIF=" in signal.summary
    assert "DEA=" in signal.summary
    assert "MACD proxy" not in signal.summary


def test_long_term_moving_averages_and_pressure_are_reported() -> None:
    long_decline = [60.0 - index * 0.16 for index in range(240)]
    weak_rebound = [21.6 + index * 0.05 for index in range(20)]
    signal = technical_signal(_bars(long_decline + weak_rebound))
    assert "MA120=" in signal.summary and "MA120=N/A" not in signal.summary
    assert "MA250=" in signal.summary and "MA250=N/A" not in signal.summary
    assert any("长周期均线下方深度压制" in item for item in signal.evidence)


def test_long_term_moving_averages_report_insufficient_samples() -> None:
    signal = technical_signal(_bars([8.0 - index * 0.02 for index in range(60)]))
    assert "MA120=N/A(样本不足)" in signal.summary
    assert "MA250=N/A(样本不足)" in signal.summary
    assert any("MA120样本不足" in item for item in signal.evidence)
    assert any("MA250样本不足" in item for item in signal.evidence)
