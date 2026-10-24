"""WealthRiskPolicy / WealthRiskEngine：财富管理专属确定性风控。

阈值来源说明（policy_sources）：初始版本由 Wealth Lab v2 研究约定，
面向中国场内基金研究演示；不构成银行适当性认定，可按需配置。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.wealth_lab.config import ASSETS

POLICY_VERSION = "wealth-risk-policy/1.0"


@dataclass(frozen=True)
class WealthRiskPolicy:
    # ASSET 类
    max_single_asset_weight: float = 0.60
    min_liquidity_20d_amount_cny: float = 20_000_000.0  # 20 日日均成交额下限（元）
    # PORTFOLIO 类
    weights_sum_tolerance: float = 1e-6
    max_weights_sum: float = 1.0        # 禁止杠杆：目标权重和不得超过 1
    max_equity_exposure: float = 0.50   # 权益类（股票/QDII 权益）暴露研究上限
    max_portfolio_drawdown: float = 0.20
    max_volatility: float = 0.25
    max_stress_loss: float = 0.15
    # DATA 类
    min_history_days: int = 250
    max_data_age_days: int = 7
    # 来源说明
    policy_sources: dict = field(default_factory=lambda: {
        "max_single_asset_weight": "Wealth Lab v2 研究约定：单一基金集中度",
        "min_liquidity_20d_amount_cny": "场内基金流动性研究下限（日均 2000 万元）",
        "max_equity_exposure": "Wealth Lab v2 研究约定：权益暴露研究上限",
        "max_portfolio_drawdown": "研究演示回撤容忍度（非适当性认定）",
        "max_stress_loss": "压力测试损失研究上限",
        "note": "阈值面向研究演示，可配置；不代表任何机构正式风控或适当性结论",
    })


@dataclass
class RiskCheckItem:
    category: str  # DATA / ASSET / PORTFOLIO / AI
    rule: str
    severity: str  # INFO / WARN / VETO
    triggered: bool
    detail: str


_VERDICT_ORDER = {"PASS": 0, "INSUFFICIENT_DATA": 1, "WARN": 2, "VETO": 3}


class WealthRiskEngine:
    """确定性硬风控。AI 解释不改变此处结果。"""

    def __init__(self, policy: WealthRiskPolicy | None = None) -> None:
        self.policy = policy or WealthRiskPolicy()

    def check(
        self,
        weights: dict[str, float],
        *,
        coverage: dict[str, dict] | None = None,
        liquidity_20d: dict[str, float] | None = None,
        metrics: dict | None = None,
        stress_loss: float | None = None,
        ai_claims: list[str] | None = None,
        computed_numbers: dict[str, float] | None = None,
    ) -> dict:
        items: list[RiskCheckItem] = []
        weights = {s: float(w) for s, w in (weights or {}).items()}
        coverage = coverage or {}
        liquidity_20d = liquidity_20d or {}
        metrics = metrics or {}
        computed_numbers = computed_numbers or {}

        # ---- DATA ----
        if not coverage:
            items.append(RiskCheckItem("DATA", "coverage_available", "VETO", False,
                                       "未提供数据覆盖信息，跳过数据类硬检查"))
        else:
            shallow = [
                s for s, cov in coverage.items()
                if cov.get("rows", 0) < self.policy.min_history_days
            ]
            stale = [
                s for s, cov in coverage.items()
                if cov.get("last") is None
            ]
            if shallow:
                items.append(RiskCheckItem(
                    "DATA", "history_depth", "VETO" if len(shallow) == len(coverage) else "WARN",
                    True, f"历史不足 {self.policy.min_history_days} 天: {shallow}"))
            else:
                items.append(RiskCheckItem("DATA", "history_depth", "INFO", False,
                                           f"全部资产 ≥{self.policy.min_history_days} 天"))
            if stale:
                items.append(RiskCheckItem("DATA", "data_available", "VETO", True,
                                           f"无法确定数据可用时间: {stale}"))

        # ---- ASSET ----
        overweight = {s: w for s, w in weights.items()
                      if w > self.policy.max_single_asset_weight + 1e-9}
        for symbol, w in overweight.items():
            items.append(RiskCheckItem(
                "ASSET", "max_single_asset_weight", "WARN", True,
                f"{symbol} 权重 {w:.2%} 超过 {self.policy.max_single_asset_weight:.0%}，削减至上限"))
        if not overweight:
            items.append(RiskCheckItem("ASSET", "max_single_asset_weight", "INFO", False, "无单资产超限"))
        illiquid = [
            s for s in weights
            if s in liquidity_20d and liquidity_20d[s] < self.policy.min_liquidity_20d_amount_cny
        ]
        if illiquid:
            items.append(RiskCheckItem("ASSET", "liquidity", "WARN", True,
                                       f"20日均成交额低于下限: {illiquid}"))
        unknown = [s for s in weights if s not in ASSETS]
        if unknown:
            items.append(RiskCheckItem("ASSET", "known_asset", "WARN", True,
                                       f"未登记资产（分类未知）: {unknown}"))

        # ---- PORTFOLIO ----
        total = sum(weights.values())
        if total > self.policy.max_weights_sum + self.policy.weights_sum_tolerance:
            items.append(RiskCheckItem("PORTFOLIO", "no_leverage", "VETO", True,
                                       f"权重和 {total:.4f} 超过 1（禁止杠杆）"))
        equity = sum(w for s, w in weights.items()
                     if ASSETS.get(s, {}).get("category")
                     in {"CN_EQUITY_ETF", "EQUITY_ETF", "QDII_ETF"})
        if equity > self.policy.max_equity_exposure + 1e-9:
            items.append(RiskCheckItem("PORTFOLIO", "equity_exposure", "VETO", True,
                                       f"权益暴露 {equity:.2%} 超过 {self.policy.max_equity_exposure:.0%}"))
        else:
            items.append(RiskCheckItem("PORTFOLIO", "equity_exposure", "INFO", False,
                                       f"权益暴露 {equity:.2%}"))
        mdd = abs(float(metrics.get("max_drawdown", 0.0)))
        if mdd > self.policy.max_portfolio_drawdown:
            items.append(RiskCheckItem("PORTFOLIO", "drawdown_limit", "WARN", True,
                                       f"回撤 {mdd:.2%} 超过 {self.policy.max_portfolio_drawdown:.0%}"))
        vol = float(metrics.get("annual_volatility", 0.0))
        if vol > self.policy.max_volatility:
            items.append(RiskCheckItem("PORTFOLIO", "volatility_limit", "WARN", True,
                                       f"年化波动 {vol:.2%} 超过 {self.policy.max_volatility:.0%}"))
        if stress_loss is not None and abs(stress_loss) > self.policy.max_stress_loss:
            items.append(RiskCheckItem("PORTFOLIO", "stress_loss", "VETO", True,
                                       f"压力损失 {stress_loss:.2%} 超过上限"))

        # ---- AI ----
        if ai_claims:
            unsupported = [
                claim for claim in ai_claims
                if not _claim_supported(claim, computed_numbers)
            ]
            if unsupported:
                items.append(RiskCheckItem("AI", "ai_claims_have_evidence", "WARN", True,
                                           f"无法核验的结论: {unsupported[:3]}"))
            else:
                items.append(RiskCheckItem("AI", "ai_claims_have_evidence", "INFO", False,
                                           f"{len(ai_claims)} 条结论均可对应计算结果"))

        # ---- 汇总与调整（削减保留为现金，不重分配）----
        verdict = "PASS"
        for item in items:
            if item.triggered and item.severity == "VETO":
                verdict = "VETO"
                break
            if item.triggered and item.severity == "WARN":
                verdict = "WARN"
        if verdict != "VETO" and coverage and all(
            cov.get("rows", 0) == 0 for cov in coverage.values()
        ):
            verdict = "INSUFFICIENT_DATA"

        adjusted = dict(weights)
        for symbol in list(adjusted):
            if adjusted[symbol] > self.policy.max_single_asset_weight:
                adjusted[symbol] = self.policy.max_single_asset_weight
        if verdict == "VETO":
            cap_total = sum(adjusted.values())
            if cap_total > self.policy.max_weights_sum + 1e-9:
                scale = self.policy.max_weights_sum / cap_total
                adjusted = {s: w * scale for s, w in adjusted.items()}
        cash_weight = max(0.0, 1.0 - sum(adjusted.values()))

        return {
            "verdict": verdict,
            "checks": [
                {"category": c.category, "rule": c.rule, "severity": c.severity,
                 "triggered": c.triggered, "detail": c.detail}
                for c in items
            ],
            "adjusted_weights": {s: round(w, 6) for s, w in adjusted.items()},
            "cash_weight_after_adjustment": round(cash_weight, 6),
            "policy_version": POLICY_VERSION,
            "policy_sources": self.policy.policy_sources,
        }


def _claim_supported(claim: str, numbers: dict[str, float]) -> bool:
    """确定性核验：结论中的关键数字必须能在计算结果中找到（容忍 5bp）。"""
    import re

    claimed = [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)\s*%", claim)]
    if not claimed:
        return True  # 无数字结论交由 AI 复核，不在此硬卡
    for value in claimed:
        matched = any(
            abs(abs(value) / 100.0 - abs(n)) <= 0.0005 for n in numbers.values()
        )
        if not matched:
            return False
    return True
