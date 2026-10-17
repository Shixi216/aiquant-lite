"""备用数据源与资金面降级单测（2026-10-15）。

覆盖（只保留实测稳定的两融/龙虎榜兜底；主力资金/财务维持 Tushare 单源）：
- backup_sources 的 两融/龙虎榜 函数结构契约（离线 mock + 在线真实调用，标注 network）
- capital_flow_enhanced 的降级触发逻辑（主源失败/不足 → 备用源；主源正常 → 不触碰备用源）
- 主力资金"单源、无兜底"的行为断言

运行：
    cd E:/hermes-opc && ./.venv/Scripts/python.exe -m pytest tests/test_backup_sources.py -q
"""
from __future__ import annotations

import os as _os

import pytest

_os.environ.setdefault("HERMES_DB_ENFORCE_OWNER", "0")


# ------------------------------------------------------------- 离线：结构契约
def test_call_with_retry_retries_then_returns():
    from data_hub.services import capital_flow_enhanced as cf

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return [1, 2, 3]

    assert cf._call_with_retry(flaky, attempts=4, base_delay=0.01) == [1, 2, 3]
    assert calls["n"] == 3


def test_call_with_retry_require_nonempty():
    from data_hub.services import capital_flow_enhanced as cf

    calls = {"n": 0}

    def empty_then_full():
        calls["n"] += 1
        return [] if calls["n"] < 2 else ["ok"]

    assert cf._call_with_retry(empty_then_full, attempts=3, require_nonempty=True, base_delay=0.01) == ["ok"]


def test_call_with_retry_all_fail_returns_none():
    from data_hub.services import capital_flow_enhanced as cf

    def always_fail():
        raise RuntimeError("nope")

    assert cf._call_with_retry(always_fail, attempts=2, base_delay=0.01) is None


# --------------------------------------- 离线：降级触发（用 monkeypatch 隔离网络）
def test_margin_falls_back_when_primary_empty(monkeypatch):
    from data_hub.services import capital_flow_enhanced as cf

    monkeypatch.setattr(cf, "_call_with_retry", lambda *a, **k: None)  # 主源空
    monkeypatch.setattr(cf, "_backup_margin", lambda symbol, days=10: [{"rzye": 1.0, "rzmre": 1.0, "rzche": 1.0, "trade_date": "20261014"}] * 5)
    rows = cf.fetch_margin("600900")
    assert len(rows) == 5  # 备用源生效


def test_margin_primary_ok_does_not_call_backup(monkeypatch):
    from data_hub.services import capital_flow_enhanced as cf
    import pandas as pd

    df = pd.DataFrame([{"trade_date": f"2026101{i}", "rzye": 1.0, "rzmre": 1.0, "rzche": 1.0} for i in range(5)])
    monkeypatch.setattr(cf, "get_tushare_pro", lambda: type("P", (), {"margin_detail": lambda self, **k: df})())
    monkeypatch.setattr(cf, "_call_with_retry", lambda fn, *a, **k: fn())
    called = {"bk": False}
    monkeypatch.setattr(cf, "_backup_margin", lambda *a, **k: called.__setitem__("bk", True) or [])
    rows = cf.fetch_margin("600900")
    assert len(rows) == 5 and called["bk"] is False


def test_moneyflow_primary_only_no_backup(monkeypatch):
    """主力资金维持 Tushare 单源（2026-10-15 管理员决定删除不稳的 ulist 兜底）。"""
    from data_hub.services import capital_flow_enhanced as cf

    monkeypatch.setattr(cf, "fetch_turnover", lambda s: None)
    monkeypatch.setattr(cf, "fetch_moneyflow", lambda s: [])
    monkeypatch.setattr(cf, "fetch_margin", lambda s: [])
    monkeypatch.setattr(cf, "fetch_top_list", lambda s: [])
    out = cf.capital_flow_factor_enhanced("600900", [])
    assert out["moneyflow"].get("net5") is None
    assert out["moneyflow"].get("source") is None


# --------------------------------------------------- 在线：真实备用源结构契约
@pytest.mark.network
def test_akshare_margin_rows_shape():
    from data_hub.services.backup_sources import akshare_margin_rows

    rows = akshare_margin_rows("600900.SH", days=4)
    if not rows:
        pytest.skip("AKShare 两融当前不可用")
    assert all("rzye" in r and "trade_date" in r for r in rows)


@pytest.mark.network
def test_akshare_top_list_rows_shape():
    from data_hub.services.backup_sources import akshare_top_list_rows

    rows = akshare_top_list_rows("002815.SZ")
    if not rows:
        pytest.skip("AKShare 龙虎榜当前不可用/未上榜")
    assert all("trade_date" in r and "net_amount" in r for r in rows)
