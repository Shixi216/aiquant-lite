from __future__ import annotations

from trading.schemas import DailyReview


def _section(
    lines: list[str],
    title: str,
    items: list[str],
    empty_message: str,
) -> None:
    lines.extend(["", f"## {title}", ""])
    if items:
        lines.extend(f"- {item}" for item in items)
    else:
        lines.append(empty_message)


def render_daily_review_markdown(review: DailyReview) -> str:
    lines = [
        f"# aiquant-lite 每日决策复盘：{review.review_date.isoformat()}（统一）",
        "",
        "> 本报告展示可审计事实、结构化结论与结果，不包含模型隐藏思维链，"
        "也不会创建或确认任何成交。",
        "",
        "## 摘要",
        "",
    ]
    lines.extend(f"- {item}" for item in review.summary)

    lines.extend(["", "## 决策记录", ""])
    if not review.decision_packets and not review.traces:
        lines.append("当日没有可比较的决策记录。")
    for packet in review.decision_packets:
        lines.extend(
            [
                f"### {packet.symbol} · DecisionPacket "
                f"{packet.decision_id} v{packet.decision_version}",
                "",
                f"- 状态：`{packet.status.value}`",
                f"- 决策：`{packet.decision.final_action.value}`",
                f"- 置信度：{packet.confidence:.0%}",
                f"- 证据质量：{packet.evidence_quality:.0%}",
                f"- 数据截止时间：{packet.data_cutoff_time.isoformat()}",
                f"- 风险标记：{len(packet.risk_flags)}",
                "",
            ]
        )
    for trace in review.traces:
        lines.extend(
            [
                f"### {trace.symbol} · 旧版决策轨迹",
                "",
                f"- 最终动作：`{trace.final_action.value}`",
                f"- 目标仓位：{trace.final_target_weight:.2%}",
                f"- 风控否决：{'是' if trace.risk_verdict.vetoed else '否'}",
                f"- 对抗复核通过：{'是' if trace.adversarial_review.passed else '否'}",
                f"- 证据条数：{len(trace.evidence_refs)}",
                "",
            ]
        )

    lines.extend(["## 模拟结果", ""])
    if not review.orders:
        lines.append("当日没有模拟委托结果。")
    for result in review.orders:
        price = "-" if result.fill_price is None else f"{result.fill_price:.4f}"
        lines.append(
            f"- {result.symbol} {result.side.value.upper()} {result.quantity} 股，"
            f"状态 `{result.status.value}`，成交价 {price}，费用 {result.fee:.2f}"
        )

    lines.extend(["", "## 用户人工成交（外部事实记录）", ""])
    if not review.manual_trades:
        lines.append("当日没有用户报告的人工成交。")
    for trade in review.manual_trades:
        decision = trade.decision_id or "未关联"
        lines.append(
            f"- {trade.symbol} {trade.side.value} {trade.quantity} 股，"
            f"价格 {trade.price:.4f}，组合 `{trade.portfolio_id}`，"
            f"DecisionPacket `{decision}`。"
        )

    lines.extend(["", "## 当前人工持仓投影", ""])
    if not review.manual_positions:
        lines.append("当前没有人工持仓投影。")
    for position in review.manual_positions:
        lines.append(
            f"- {position.symbol}：数量 {position.quantity}，"
            f"平均成本 {position.average_cost:.4f}，"
            f"已实现损益 {position.realized_pnl:.2f}。"
        )

    lines.extend(["", "## 人工持仓风险复核", ""])
    if not review.manual_position_risk_reviews:
        lines.append("当日没有人工持仓风险复核。")
    for risk_review in review.manual_position_risk_reviews:
        lines.append(
            f"- 持仓 `{risk_review.position_id}`：风险 "
            f"`{risk_review.risk_level.value}`，原始逻辑 "
            f"`{risk_review.original_thesis_status.value}`，建议 "
            f"`{risk_review.recommended_action.value}`，证据 "
            f"{len(risk_review.evidence_record_ids)} 条。"
        )

    _section(
        lines,
        "计划偏离与决策偏差",
        review.decision_deviations,
        "未发现可识别的计划偏离。",
    )
    _section(
        lines,
        "模型问题",
        review.model_errors,
        "未记录到可识别的模型问题。",
    )
    _section(
        lines,
        "证据缺失",
        review.evidence_gaps,
        "未记录到证据缺失。",
    )
    _section(
        lines,
        "数据质量",
        review.data_quality_issues,
        "未记录到数据质量问题。",
    )
    lines.extend(
        [
            "",
            "## 安全边界",
            "",
            "- 本复盘只读决策、模拟、人工成交事实、持仓投影和风险审计记录。",
            "- 本复盘不会创建模拟成交、人工成交或修改任何持仓。",
            "- 所有建议仅供人工判断，不能作为可执行交易指令。",
            "",
        ]
    )
    return "\n".join(lines)
