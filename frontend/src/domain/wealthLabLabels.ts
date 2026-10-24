/**
 * 财富实验室统一中文标签映射（v1 + v2 共用唯一来源）。
 * 规则：面向用户的一切文案取自本文件；API 字段名、strategy_id、哈希等保留英文原值。
 * 禁止在组件内重复硬编码同义标签。
 */

import type { DataMode } from "../types/wealthLab";
import type { AiReviewVerdict, RiskVerdict, V2AssetCategory } from "../types/wealthLabV2";

/* ---------------- 数据模式 ---------------- */

export const DATA_MODE_LABELS: Record<DataMode, string> = {
  REAL: "真实数据",
  DEMO: "演示数据",
  UNAVAILABLE: "数据不可用",
};

/** 徽章样式约定：REAL 绿 / DEMO 与 UNAVAILABLE 橙。 */
export function dataModeBadge(mode: DataMode | undefined | null): { text: string; cls: string } {
  if (!mode) return { text: "等待回测", cls: "badge" };
  if (mode === "REAL") return { text: "真实数据", cls: "badge ok" };
  if (mode === "DEMO") return { text: "演示数据", cls: "badge warn" };
  return { text: "数据不可用", cls: "badge warn" };
}

/* ---------------- 通用指标（v1 Metrics / v2 V2Metrics） ---------------- */

export const METRIC_LABELS = {
  total_return: "累计收益率",
  annual_return: "年化收益率",
  annual_volatility: "年化波动率",
  sharpe_ratio: "夏普比率 Sharpe",
  sortino_ratio: "索提诺比率 Sortino",
  calmar_ratio: "卡玛比率 Calmar",
  max_drawdown: "最大回撤",
  monthly_win_rate: "月度胜率",
  avg_turnover_per_rebalance: "单次调仓平均换手",
  trading_days: "交易日数",
} as const;

export type MetricKey = keyof typeof METRIC_LABELS;

/** 夏普/索提诺/卡玛的通俗解释（悬浮提示用）。 */
export const METRIC_HINTS: Partial<Record<MetricKey, string>> = {
  sharpe_ratio: "每承担 1 单位总波动所获得的超额收益（无风险利率 2%/年）",
  sortino_ratio: "每承担 1 单位下行波动所获得的超额收益，只惩罚亏损方向的波动",
  calmar_ratio: "年化收益 ÷ 最大回撤绝对值，衡量回撤修复能力",
  max_drawdown: "净值从历史高点回落的最大幅度",
  avg_turnover_per_rebalance: "每次调仓时买卖资产占组合的比例，换手越高交易成本越高",
};

/* ---------------- 因子（v2 多因子研究） ---------------- */

export const FACTOR_LABELS = {
  momentum_126d: "126日动量",
  trend_score: "趋势强度（价格/SMA200）",
  volatility_63d: "63日波动率",
  max_drawdown_252d: "252日最大回撤",
  liquidity_20d_avg_amount_cny: "20日日均成交额",
  composite: "综合因子得分",
} as const;

export const FACTOR_HINTS = {
  momentum_126d: "过去 126 个交易日（约半年）的区间收益率",
  trend_score: "最新价格相对 200 日均线的偏离度，正值代表处于均线上方",
  volatility_63d: "近 63 个交易日（约一季度）收益率的年化波动",
  max_drawdown_252d: "过去一年净值从高点的最大回落",
  liquidity_20d_avg_amount_cny: "近 20 日日均成交金额，代表流动性厚度",
  composite: "五因子标准化加权后的综合得分，正值代表综合表现强，可为负",
} as const;

/**
 * composite_rank 展示口径判定：
 * 契约定义为 0-1 分位，但真实数据可能返回 z-score 型得分（可负）。
 * 全部落在 [0,1] 时按"综合分位"展示，否则按"综合因子得分"展示——只改名称，不改数值。
 */
export function compositeMetric(values: number[]): { label: string; hint: string } {
  const usable = values.filter((v) => Number.isFinite(v));
  const rankLike = usable.length > 0 && usable.every((v) => v >= 0 && v <= 1 + 1e-9);
  return rankLike
    ? { label: "综合分位", hint: "0-1 截面分位，越接近 1 越强" }
    : { label: "综合因子得分", hint: FACTOR_HINTS.composite };
}

/* ---------------- 风控 / AI 复核 ---------------- */

export const RISK_VERDICT_LABELS: Record<RiskVerdict, string> = {
  PASS: "检查通过",
  WARN: "风险警告",
  VETO: "风险否决",
  INSUFFICIENT_DATA: "数据不足",
};

/** 醒目裁决色：通过绿 / 警告橙 / 否决红 / 数据不足灰。 */
export const RISK_VERDICT_CLS: Record<RiskVerdict, string> = {
  PASS: "badge ok",
  WARN: "badge warn",
  VETO: "badge veto",
  INSUFFICIENT_DATA: "badge",
};

export const RISK_CATEGORY_LABELS: Record<string, string> = {
  DATA: "数据",
  ASSET: "资产",
  PORTFOLIO: "组合",
  AI: "AI 结论",
};

export const RISK_SEVERITY_LABELS: Record<string, string> = {
  INFO: "提示",
  WARN: "警告",
  VETO: "否决",
};

/** 风险规则代码 → 中文名称（未登记规则回退显示原代码；后端 rule 可能含/不含比较符，两种键都登记）。 */
export const RISK_RULE_LABELS: Record<string, string> = {
  "history_depth": "历史数据深度",
  "history_depth>=252": "历史数据深度 ≥ 252 个交易日",
  "max_single_asset_weight": "单一资产权重上限",
  "max_single_asset_weight<=0.6": "单一资产权重 ≤ 60%",
  "equity_exposure": "权益类资产暴露",
  "equity_exposure<=0.5": "权益类资产暴露 ≤ 50%",
  "ai_claims_have_evidence": "AI 结论具备计算依据",
};

export function riskRuleLabel(rule: string): string {
  return RISK_RULE_LABELS[rule] ?? rule;
}

/** AI 复核裁决的准确口径说明：SUPPORTED 是一致性核验，不是收益证明。 */
export const AI_REVIEW_VERDICT_NOTES: Record<AiReviewVerdict, string> = {
  SUPPORTED: "该裁决表示 AI 结论与现有确定性计算证据相互一致；此为一致性核验结果，不构成对未来收益真实性的证明。",
  NEEDS_MORE_EVIDENCE: "部分结论暂时找不到可核对的计算依据，需要补充样本外验证后再采信。",
  CONTRADICTED: "存在与确定性计算结果相矛盾的结论，采信前必须先修正。",
};

export const AI_REVIEW_VERDICT_LABELS: Record<AiReviewVerdict, string> = {
  SUPPORTED: "结论成立",
  NEEDS_MORE_EVIDENCE: "证据不足",
  CONTRADICTED: "存在矛盾",
};

/* ---------------- 资产类别 / 优化器 / 策略参数 ---------------- */

export const V2_CATEGORY_LABELS: Record<V2AssetCategory, string> = {
  CN_EQUITY_ETF: "权益 ETF",
  CN_BOND_ETF: "债券 ETF",
  CN_GOLD_ETF: "黄金 ETF",
  CN_CONVERTIBLE_ETF: "可转债 ETF",
  CN_REIT: "REITs",
  QDII_ETF: "QDII ETF",
};

/** 优化器方法统一名称。 */
export const OPTIMIZATION_METHOD_LABELS: Record<string, string> = {
  min_vol: "最小波动率",
  max_sharpe: "最大夏普比率",
  hrp: "层次风险平价 HRP",
};

export const STRATEGY_PARAM_LABELS: Record<string, string> = {
  weights: "目标权重",
  lookback_days: "动量回看（日）",
  top_k: "持有数量",
  min_momentum: "动量下限",
  defensive: "防御资产",
  risky: "进攻资产池",
  window_days: "估计窗口（日）",
  max_weight: "单资产权重上限",
  sma_days: "均线周期（日）",
  risk_free_rate: "无风险利率",
};

export const WEIGHT_SNAPSHOT_LABELS = {
  date: "调仓日",
  decision_data_through: "决策数据截止日",
  weights: "目标权重",
} as const;

/** 现金权重的用户解释。 */
export const CASH_WEIGHT_HINT = "现金权重 = 组合中未投入任何 ETF 的现金比例；策略信号不足或历史窗口不够时自动持有现金";

/** 交易成本的用户解释（契约：Σ|Δw|×cost_bps/1e4）。 */
export const COST_MODEL_HINT = "交易成本按每次调仓的换手幅度 × 单边费率（bps）计提，双边合计";
