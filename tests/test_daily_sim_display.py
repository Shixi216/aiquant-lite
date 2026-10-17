"""模拟盘快照展示层单测（2026-10-16 A 组改动自检）。

覆盖 A1-A6 的展示层改动（不改任何业务/评分逻辑）：
- A1 两套分标注来源口径
- A2 分组规则补全（含硬化关）+ 被剔名单上屏
- A3 动作标明为「候选级动作」
- A4 市场情绪摘要视觉分离（另一来源）
- A5 策略组标的明细
- A6 硬化剔除落盘（state["harden_dropped"]）

做法：monkeypatch 掉重活（冻结/取数/Router），只驱动 main() 的展示路径，断言输出文本。
不触碰真实 reports/sim_daily（REPORTS_DIR 指向 tmp）。

运行：
    cd E:/hermes-opc && ./.venv/Scripts/python.exe -m pytest tests/test_daily_sim_display.py -q
"""
from __future__ import annotations

import sys

import pytest

import scripts.daily_sim_snapshot as sim


def _fake_result(symbol: str, name: str, group: str, action: str, score: float,
                 csec: float, ret: float) -> dict:
    return {
        "symbol": symbol, "name": name, "status": "OK", "决策时间": "14:00",
        "当前动作": action, "总评分": score,
        "五维结论": {"技术": 0.9, "基本": 0.0, "情绪": None, "政策": None, "资金": None},
        "盘中强度": "强", "波段质量": "中等", "仓位建议": "模拟观察",
        "参考价格": 10.0, "后续表现": {"信号价": 9.0, "当前价": 10.0, "涨跌幅": ret},
        "止损止盈": None, "VETO状态": "NO_VETO", "相比上次变化": "", "变化原因": "",
        "有效期限": "当日", "观察组": group, "截面分": csec, "当日涨跌": ret,
    }


@pytest.fixture()
def stub_main(monkeypatch, tmp_path):
    monkeypatch.setattr(sim, "REPORTS_DIR", tmp_path / "sim_daily")
    monkeypatch.setattr(sim, "_load_state", lambda d: {})
    monkeypatch.setattr(sim, "_save_state", lambda d, s: None)
    monkeypatch.setattr(sim, "_tencent_prices", lambda syms: {})
    monkeypatch.setattr(sim, "_market_benchmark", lambda: {"中证全指": 0.34, "沪深300": -0.10})
    monkeypatch.setattr(sim, "_read_sector_context", lambda: {})
    # B3半步（2026-10-16）：stub 掉 Router 取正式动作，保证单测不走网络
    monkeypatch.setattr(sim, "_router_formal_action",
                        lambda sym: {"action": "等待", "status": "等待确认"})
    # B2旁路（2026-10-16）：stub 掉行业打标，保证单测不碰库
    def fake_tag(frozen):
        n = 0
        for f in frozen:
            if "行业" not in f:
                f["行业"] = "水上运输"
                f["板块昨日强度"] = 1.2
                n += 1
        return n
    monkeypatch.setattr(sim, "_attach_sector_strength", fake_tag)

    captured_state: dict = {}

    def fake_freeze(state):
        state["group_rule"] = "命中组 = 截面分≥buy_q 且 过硬化关(决策关+交叉验证关)；被剔者移入临界组"
        state["harden_dropped"] = [
            {"symbol": "601872.SH", "name": "招商轮船", "reject": "龙虎榜区间净卖-1.19亿", "截面分": 0.735},
        ]
        captured_state.update(state)
        return [
            {"symbol": "601811.SH", "name": "新华文轩", "group": "命中组", "price": 25.58},
            {"symbol": "600316.SH", "name": "洪都航空", "group": "临界组", "price": 35.10},
        ]

    monkeypatch.setattr(sim, "_freeze_top20", fake_freeze)

    def fake_research(symbol, name, price_hint=None, tencent=None):
        if symbol == "601811.SH":
            return _fake_result(symbol, name, "命中组", "强势研究候选", 0.553, 0.577, 8.76)
        return _fake_result(symbol, name, "临界组", "强势研究候选", 0.465, 0.208, 6.47)

    monkeypatch.setattr(sim, "_research_one", fake_research)

    (tmp_path / "sim_daily" / "20261016").mkdir(parents=True, exist_ok=True)
    yield captured_state


def test_display_layer_labels(stub_main, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daily_sim_snapshot.py", "--timepoint", "pm14"])
    sim.main()
    out = capsys.readouterr().out

    # A1 两套分口径标注
    assert "截面分(横截面·盘前)" in out
    assert "正式评分(请求时重算)" in out
    assert "两者口径不同、方向可能相反" in out
    # A3 候选级动作
    assert "候选级动作分布" in out
    assert "候选动作:" in out
    # B3半步：候选动作 + 正式动作 并列展示（不替换）
    assert "正式动作:" in out and "正式动作:—" not in out
    assert "正式动作 = DecisionEngine 正式结论" in out
    # B2旁路：行业+昨日板块强度打标（仅记录）
    assert "板块旁路打标" in out and "水上运输" in out
    # A2 分组规则补全 + 被剔名单上屏
    assert "过硬化关(决策关+交叉验证关)" in out
    assert "本时点被硬化剔除" in out and "招商轮船" in out
    # A4 情绪摘要视觉分离
    assert "【另一来源·独立摘要】市场情绪观察" in out
    assert "摘要结束，以下为本快照内容" in out
    # A5 策略组明细
    assert "策略组标的明细" in out
    assert "新华文轩(601811)" in out and "洪都航空(600316)" in out
    # 歧义标签已改口径
    assert "命中组(正式评分≥0.3)" in out
    assert "命中组(综合分≥0.3)" not in out


def test_harden_dropped_persisted_to_state(stub_main, monkeypatch):
    # A6：硬化剔除写入 state，供 _save_state 落盘（此处 stub 了 save，故直接断言 state）
    monkeypatch.setattr(sys, "argv", ["daily_sim_snapshot.py", "--timepoint", "pm14"])
    sim.main()
    assert stub_main.get("harden_dropped")
    assert stub_main["harden_dropped"][0]["name"] == "招商轮船"
    assert "group_rule" in stub_main
