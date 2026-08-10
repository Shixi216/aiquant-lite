"""对话入口整合（阶段4.3 / 任务书第二十、二十一章）— 全中文输出

企业微信/QQ 统一决策输出格式。
所有字段标签中文化，所有英文枚举值翻译为中文。
"""
from __future__ import annotations

from trading.decision_support.decision_response import DecisionResponse
from trading.decision_support.data_status import data_status_zh

# 动作强度中文化
ACTION_STRENGTH_ZH = {
    "strong": "强",
    "normal": "普通",
    "weak": "弱",
}

# 状态中文化
STATUS_ZH = {
    "FRESH": "数据新鲜",
    "DEGRADED": "数据降级",
    "STALE": "数据陈旧",
    "FAILED": "数据失败",
    "CONSISTENT": "一致",
    "INCONSISTENT": "不一致",
    "NA": "不适用",
    "NO_VETO": "未触发",
}

# 动作中文化（兜底映射，与 action.py 一致）
ACTION_ZH_FULL = {
    "STRONG_BUY": "强烈建仓", "BUY": "建仓", "SMALL_BUY": "小仓试错",
    "WAIT": "等待", "AVOID": "回避", "ADD": "加仓", "HOLD": "持有",
    "REDUCE": "减仓", "TAKE_PROFIT": "止盈", "STOP_LOSS": "止损",
    "EXIT": "清仓",
}


def _zh_strength(s: str) -> str:
    return ACTION_STRENGTH_ZH.get(s, s)


def _zh_status(s: str) -> str:
    return STATUS_ZH.get(s, s)


def format_decision_response(resp: DecisionResponse) -> str:
    """把 DecisionResponse 格式化为对话输出（全中文，先结论后数据）"""
    lines = []
    lines.append(f"【当前建议】{resp.action_zh}")
    # 正式动作与执行状态拆分
    exec_status = resp.execution_status or "等待确认"
    lines.append(f"正式动作: {resp.action_zh} | 执行状态: {exec_status}")
    lines.append(f"动作强度: {_zh_strength(resp.action_strength)} | 置信度: {resp.confidence:.2f}")
    lines.append(f"数据状态: {data_status_zh(resp.data_status)}"
                 + (f" | 快照编号: {resp.snapshot_id}" if resp.snapshot_id else ""))
    lines.append(f"交易日期: {resp.trade_date}" if resp.trade_date else "")
    lines.append(f"数据截止: {resp.data_cutoff}" if resp.data_cutoff else "")

    # 仓位建议
    pos_line = f"建议仓位: 当前 {resp.current_position_ratio:.0%} → 目标 {resp.target_position_ratio:.0%}"
    if abs(resp.position_change_ratio) > 0.001:
        pos_line += f"（调整 {resp.position_change_ratio:+.0%}，分{resp.recommended_batches}次）"
    lines.append(pos_line)
    # 首批仓位（建仓类动作时显示）
    if resp.allow_new_position and resp.recommended_batches > 0:
        first_batch = resp.target_position_ratio / resp.recommended_batches
        lines.append(f"首批仓位: {first_batch:.0%}（目标÷批次）")

    # 交易区间
    pz = resp.price_zones
    if pz.preferred_zone:
        lines.append(f"优选建仓区间: {pz.preferred_zone[0]:.2f}至{pz.preferred_zone[1]:.2f}元")
    if pz.entry_zone:
        lines.append(f"建仓区间: {pz.entry_zone[0]:.2f}至{pz.entry_zone[1]:.2f}元")
    if pz.add_zone:
        lines.append(f"加仓区间: {pz.add_zone[0]:.2f}至{pz.add_zone[1]:.2f}元")
    if pz.reduce_zone:
        lines.append(f"减仓区间: {pz.reduce_zone[0]:.2f}至{pz.reduce_zone[1]:.2f}元")
    if pz.take_profit_zone:
        lines.append(f"止盈区间: {pz.take_profit_zone[0]:.2f}至{pz.take_profit_zone[1]:.2f}元")
    if pz.stop_loss_price is not None:
        cond = pz.stop_loss_condition or "收盘跌破止损价"
        lines.append(f"止损条件: {pz.stop_loss_price:.2f}元（{cond}）")
    if pz.invalidation_condition:
        lines.append(f"失效条件: {pz.invalidation_condition}")
    if pz.expected_holding_period:
        lines.append(f"预期周期: {pz.expected_holding_period}")

    # 评分
    score_line = f"正式评分: {resp.formal_score:+.2f}"
    if resp.enhanced_score is not None:
        score_line += f" | 五维增强分: {resp.enhanced_score:+.2f}"
    score_line += f" | 置信度: {resp.confidence:.2f}"
    lines.append(score_line)
    if resp.consistency_status == "INCONSISTENT":
        lines.append(f"⚠️ 一致性: 五维增强与正式评分不一致（{resp.inconsistency_reason}）")

    # 允许操作标志
    flags = []
    if resp.allow_new_position:
        flags.append("允许建仓")
    if resp.allow_add_position:
        flags.append("允许加仓")
    if resp.recommend_reduce:
        flags.append("建议减仓")
    if resp.recommend_exit:
        flags.append("建议清仓")
    if flags:
        lines.append("操作许可: " + "、".join(flags))

    # VETO
    if resp.veto_triggered:
        lines.append("🚨 硬性风控已触发: 禁止建仓"
                     + ("（已持仓→减仓/清仓）" if resp.recommend_exit else ""))

    # 风险
    if resp.major_risks:
        lines.append("主要风险: " + "、".join(resp.major_risks[:3]))

    # 数据缺失
    if resp.missing_data:
        lines.append("缺失数据: " + "、".join(resp.missing_data))

    # 下一步
    if resp.next_action:
        lines.append(f"下一步: {resp.next_action}")

    lines.append("⚠️ 价格区间是交易计划，不是自动委托。")
    return "\n".join(lines)


def format_simple_conclusion(resp: DecisionResponse) -> str:
    """极简结论（手机阅读友好，全中文）"""
    lines = [
        f"当前建议：{resp.action_zh}。",
        f"建议仓位：{resp.target_position_ratio:.0%}。",
    ]
    pz = resp.price_zones
    if pz.entry_zone:
        lines.append(f"建仓区间：{pz.entry_zone[0]:.2f}至{pz.entry_zone[1]:.2f}元。")
    if pz.stop_loss_price is not None:
        lines.append(f"止损：{pz.stop_loss_price:.2f}元。")
    lines.append(f"正式评分：{resp.formal_score:+.2f}。")
    lines.append(f"置信度：{resp.confidence:.2f}。")
    if resp.major_risks:
        lines.append(f"主要风险：{resp.major_risks[0]}。")
    lines.append("是否创建交易计划？")
    return "\n".join(lines)


def translate_decision_fields(data: dict) -> dict:
    """把 DecisionResponse.to_dict() 的英文字段名/值翻译为中文"""
    field_map = {
        "data_status": "数据状态", "data_status_zh": "数据状态",
        "trade_date": "交易日期", "collected_at": "采集时间",
        "data_cutoff": "数据截止", "snapshot_id": "快照编号",
        "coverage_ratio": "覆盖率", "action": "动作",
        "action_zh": "动作", "action_strength": "动作强度",
        "allow_new_position": "允许建仓", "allow_add_position": "允许加仓",
        "recommend_reduce": "建议减仓", "recommend_exit": "建议清仓",
        "veto_triggered": "风控触发", "formal_score": "正式评分",
        "enhanced_score": "五维增强分", "confidence": "置信度",
        "consistency_status": "一致性", "inconsistency_reason": "不一致原因",
        "current_position_ratio": "当前仓位", "target_position_ratio": "目标仓位",
        "position_change_ratio": "仓位变化", "recommended_batches": "分批次数",
        "price_zones": "价格区间", "supporting_reasons": "决策依据",
        "major_risks": "主要风险", "missing_data": "缺失数据",
        "next_action": "下一步",
        "execution_status": "执行状态",
    }
    translated = {}
    for k, v in data.items():
        new_k = field_map.get(k, k)
        # 值翻译
        if k == "action_strength" and isinstance(v, str):
            v = _zh_strength(v)
        elif k == "action" and isinstance(v, str):
            v = ACTION_ZH_FULL.get(v, v)
        elif k == "consistency_status" and isinstance(v, str):
            v = _zh_status(v)
        elif k == "veto_triggered":
            v = "是" if v else "否"
        elif k == "price_zones" and isinstance(v, dict):
            # 价格区间内嵌字段翻译
            zone_map = {
                "entry_zone": "建仓区间", "add_zone": "加仓区间",
                "preferred_zone": "优选建仓区间",
                "reduce_zone": "减仓区间", "take_profit_zone": "止盈区间",
                "stop_loss_price": "止损价", "stop_loss_condition": "止损条件",
                "invalidation_condition": "失效条件",
                "expected_holding_period": "预期周期",
            }
            v = {zone_map.get(zk, zk): zv for zk, zv in v.items()}
        elif isinstance(v, bool):
            v = "是" if v else "否"
        translated[new_k] = v
    return translated


def assert_action_consistency(
    report_action: str, decision_response_action: str, source: str = "报告层"
) -> None:
    """一致性断言：报告中的正式动作必须等于 DecisionResponse.action

    Args:
        report_action: 报告层输出的动作（中文）
        decision_response_action: DecisionResponse.action_zh 的值
        source: 报告来源标识

    Raises:
        AssertionError: 动作不一致时抛出
    """
    # 反查中文→英文枚举，再正查英文→中文，确保双向一致
    zh_to_en = {v: k for k, v in ACTION_ZH_FULL.items()}
    report_en = zh_to_en.get(report_action, report_action)
    decision_en = zh_to_en.get(decision_response_action, decision_response_action)
    if report_en != decision_en:
        raise AssertionError(
            f"[一致性断言失败] {source}输出动作「{report_action}」"
            f"与决策引擎动作「{decision_response_action}」不一致。"
            f"正式动作必须来自 DecisionEngine.decide()，"
            f"报告层不得自行改写。"
        )
