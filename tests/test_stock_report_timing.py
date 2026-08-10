from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from trading.schemas import Bar


def test_stock_report_times_all_stages_and_never_repeats_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading.research.stock_report_service as module

    bars = [
        Bar(
            trade_date=date(2026, 8, 10),
            open=10.0,
            high=10.8,
            low=9.8,
            close=10.5,
            volume=1000.0,
        )
    ]
    calls = {"fetch": 0, "load": 0, "decision": 0}

    def fake_fetch(symbol, days, *, timings):
        calls["fetch"] += 1
        for stage in ("行情", "K线", "财务", "公告", "新闻", "数据库"):
            timings.record(stage, 0.001)

    def fake_load(self, symbol):
        calls["load"] += 1
        return bars, [], [], []

    class FakeDecisionEngine:
        def __init__(self, **kwargs):
            pass

        def decide(self, decision_input):
            calls["decision"] += 1
            return SimpleNamespace(
                action_zh="等待",
                execution_status="等待确认",
                next_action="等待价格进入区间或信号确认",
            )

    monkeypatch.setattr(module, "fetch_and_persist", fake_fetch)
    monkeypatch.setattr(module.StockReportService, "_load_inputs", fake_load)
    monkeypatch.setattr(module, "_check_freshness", lambda symbol, values: False)
    monkeypatch.setattr(
        module,
        "technical_factor",
        lambda values: {"score": 0.2, "confidence": 0.8, "summary": "技术正常"},
    )
    monkeypatch.setattr(
        module,
        "fundamental_factor",
        lambda values: {"score": 0.1, "confidence": 0.6, "summary": "财务正常"},
    )
    monkeypatch.setattr(
        module,
        "capital_flow_factor_enhanced",
        lambda symbol, values: {"score": 0.0, "pv_label": "平稳", "label": "中性"},
    )
    monkeypatch.setattr(
        module,
        "sentiment_factor_ai",
        lambda symbol, news, cutoff: {"score": 0.0, "events": []},
    )
    monkeypatch.setattr(
        module,
        "policy_factor",
        lambda announcements: {"score": 0.0, "notes": [], "total_announcements": 0},
    )
    monkeypatch.setattr(module, "DecisionEngine", FakeDecisionEngine)

    report = module.StockReportService().run(symbol="600172", days=90)

    assert calls == {"fetch": 1, "load": 1, "decision": 1}
    assert "STALE（未重复执行完整分析流程）" in report
    for stage in module.TIMING_STAGES:
        assert f"  {stage}: " in report
    assert "正式动作(DecisionEngine): 等待" in report
