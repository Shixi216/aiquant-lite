from __future__ import annotations

from trading.schemas import DailyReview


def render_daily_review_markdown(review: DailyReview) -> str:
    lines = [
        f"# aiquant-lite 每日决策复盘：{review.review_date.isoformat()}",
        "",
        "> 本报告展示证据、结构化观点、风控结论和操作结果；不包含模型隐藏思维链。",
        "",
        "## 摘要",
        "",
    ]
    lines.extend(f"- {item}" for item in review.summary)
    lines.extend(["", "## 决策记录", ""])
    if not review.traces:
        lines.append("当日没有决策记录。")
    for trace in review.traces:
        lines.extend(
            [
                f"### {trace.symbol} · {trace.final_action.value.upper()}",
                "",
                f"- 目标仓位：{trace.final_target_weight:.2%}",
                f"- 风控否决：{'是' if trace.risk_verdict.vetoed else '否'}",
                f"- 对抗复核通过：{'是' if trace.adversarial_review.passed else '否'}",
                f"- 证据条数：{len(trace.evidence_refs)}",
                "",
                "结构化观点：",
                "",
            ]
        )
        for opinion in trace.opinions:
            lines.append(
                f"- `{opinion.role}`：{opinion.stance}，置信度 {opinion.confidence:.0%}；"
                f"{opinion.summary}"
            )
        if trace.risk_verdict.reasons:
            lines.extend(["", "风控原因：", ""])
            lines.extend(f"- {reason}" for reason in trace.risk_verdict.reasons)
        if trace.adversarial_review.findings:
            lines.extend(["", "对抗审查发现：", ""])
            lines.extend(f"- {finding}" for finding in trace.adversarial_review.findings)
        lines.append("")
    lines.extend(["## 模拟订单", ""])
    if not review.orders:
        lines.append("当日没有模拟订单。")
    for order in review.orders:
        price = "-" if order.fill_price is None else f"{order.fill_price:.4f}"
        lines.append(
            f"- {order.symbol} {order.side.value.upper()} {order.quantity} 股，"
            f"状态 `{order.status.value}`，成交价 {price}，费用 {order.fee:.2f}"
        )
    lines.append("")
    return "\n".join(lines)
