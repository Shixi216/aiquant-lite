/**
 * 1:1 镜像 docs/contracts/wealth-lab-api-v2.yaml（策略工厂/优化/风控/AI 扩展）。
 * 信封与 v1 完全一致，直接复用 types/wealthLab 的 EnvelopeFields/WealthEnvelope/DataMode。
 * 冻结条款同 v1：字段变更必须先升契约版本并同步本文件。
 */

import type { DataMode, EnvelopeFields, RebalanceFrequency, WealthEnvelope } from "./wealthLab";

export type { DataMode, WealthEnvelope };
export type V2AssetCategory =
  | "CN_EQUITY_ETF"
  | "CN_BOND_ETF"
  | "CN_GOLD_ETF"
  | "CN_CONVERTIBLE_ETF"
  | "CN_REIT"
  | "QDII_ETF";

/* ---------------- universe ---------------- */

export interface UniverseAsset {
  symbol: string;
  name: string;
  category: V2AssetCategory;
  exchange: "SSE" | "SZSE";
  currency: "CNY";
  listing_date: string;
  data_first_date: string | null;
  data_last_date: string | null;
  data_rows: number;
  data_status: DataMode;
  source_breakdown: Record<string, number>;
  risk_notes: string;
}

export interface UniverseData {
  data_mode: DataMode;
  assets: UniverseAsset[];
}

/* ---------------- factors ---------------- */

export interface FactorScore {
  symbol: string;
  asof: string;
  /** 126日收益率（小数）。 */
  momentum_126d: number;
  /** 价格/SMA200 - 1（小数，可为负）。 */
  trend_score: number;
  volatility_63d: number;
  max_drawdown_252d: number;
  /** 20日日均成交额（元）。 */
  liquidity_20d_avg_amount_cny: number;
  /** 综合分位 0-1。 */
  composite_rank: number;
}

export interface FactorsData {
  data_mode: DataMode;
  asof: string;
  factors: FactorScore[];
}

/* ---------------- strategies ---------------- */

export interface StrategyMeta {
  strategy_id: string;
  strategy_version: string;
  name: string;
  description: string;
  params: Record<string, unknown>;
  min_history_days: number;
  rebalance: RebalanceFrequency;
  risk_notes: string;
  asset_selection: string;
}

export interface StrategiesData {
  data_mode: DataMode;
  strategies: StrategyMeta[];
}

export interface StrategyBacktestPayload {
  params_override?: Record<string, unknown>;
  symbols?: string[];
  start_date?: string;
  end_date?: string;
  cost_bps?: number;
  initial_capital?: number;
}

export interface StrategySeries {
  dates: string[];
  nav: number[];
  drawdown: number[];
  cash_weight: number[];
  demo: boolean;
}

export interface V2Metrics {
  total_return: number;
  annual_return: number;
  annual_volatility: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  max_drawdown: number;
  monthly_win_rate: number;
  avg_turnover_per_rebalance: number;
  trading_days: number;
}

export interface V2RebalanceTrade {
  symbol: string;
  weight_before: number;
  weight_after: number;
  turnover: number;
}

export interface V2RebalanceEvent {
  date: string;
  trades: V2RebalanceTrade[];
  cost: number;
}

/** 每个调仓日目标权重快照（防未来函数可审计）。 */
export interface WeightSnapshot {
  date: string;
  /** 决策所用数据截止（含）。 */
  decision_data_through: string;
  weights: Record<string, number>;
}

export interface StrategyBacktestData {
  data_mode: DataMode;
  backtest_id: string;
  strategy: StrategyMeta;
  series: StrategySeries;
  metrics: V2Metrics;
  weight_history: WeightSnapshot[];
  rebalances: V2RebalanceEvent[];
  data_quality: {
    adjusted: boolean;
    sources: string[];
    missing_days: number;
    caveats: string[];
  };
}

/* ---------------- compare ---------------- */

export interface ComparePayload {
  strategy_ids: string[];
  start_date?: string;
  end_date?: string;
  cost_bps?: number;
}

export interface CompareWindow {
  start_date: string;
  end_date: string;
  cost_bps: number;
}

export interface StrategyCompareRow {
  strategy_id: string;
  metrics: V2Metrics;
}

export interface CompareData {
  data_mode: DataMode;
  window: CompareWindow;
  comparison: StrategyCompareRow[];
}

/* ---------------- optimization ---------------- */

export type OptimizationMethod = "min_vol" | "max_sharpe" | "hrp";

export interface OptimizationPayload {
  method: OptimizationMethod;
  symbols?: string[];
  /** 估计窗口（只用截至 asof 的历史）。 */
  estimation_window_days?: number;
  /** 决策时点（缺省为最新数据日）。 */
  asof?: string;
  risk_free_rate?: number;
  adapter?: "auto" | "pypfopt" | "native";
}

export interface OptimizationData {
  data_mode: DataMode;
  method: string;
  /** pypfopt 或 native（纯 numpy）。 */
  adapter_used: string;
  asof: string;
  /** 实际使用的估计区间 [start..end]。 */
  estimation_window: string;
  weights: Record<string, number>;
  cash_weight: number;
  /** 窗口内估计值（非预测承诺）。 */
  expected_annual_return: number;
  estimated_annual_volatility: number;
  estimated_sharpe: number;
  notes: string[];
}

/* ---------------- risk check ---------------- */

export type RiskVerdict = "PASS" | "WARN" | "VETO" | "INSUFFICIENT_DATA";

export interface RiskCheckPayload {
  weights: Record<string, number>;
  backtest_id?: string;
  portfolio_context?: {
    current_drawdown?: number;
    stress_loss_recent?: number;
    ai_claims?: string[];
  };
}

export interface RiskCheckItem {
  category: "DATA" | "ASSET" | "PORTFOLIO" | "AI";
  rule: string;
  severity: "INFO" | "WARN" | "VETO";
  triggered: boolean;
  detail: string;
}

export interface RiskCheckData {
  data_mode: DataMode;
  verdict: RiskVerdict;
  checks: RiskCheckItem[];
  /** 被削减权重保留为现金，不自动重分配。 */
  adjusted_weights: Record<string, number>;
  cash_weight_after_adjustment: number;
  policy_version: string;
}

/* ---------------- AI research ---------------- */

export interface AiResearchPayload {
  strategy_id?: string;
  backtest_id?: string;
  /** 补充研究关注点（可选）。 */
  context?: string;
}

export interface AiResearchData {
  data_mode: DataMode;
  research_id: string;
  model: string;
  model_call_ids: string[];
  prompt_version: string;
  summary: string;
  strategy_differences: string[];
  key_risks: string[];
  counter_evidence: string[];
  data_gaps: string[];
  used_backtest_id: string;
  schema_valid: true;
}

export type AiReviewVerdict = "SUPPORTED" | "NEEDS_MORE_EVIDENCE" | "CONTRADICTED";

export interface AiRiskReviewPayload {
  research_id?: string;
  claims?: string[];
}

export interface AiRiskReviewData {
  data_mode: DataMode;
  review_id: string;
  model: string;
  verdict: AiReviewVerdict;
  unchecked_claims: string[];
  hallucination_flags: string[];
  human_review_required: boolean;
  notes: string;
}

/* ---------------- decision packet ---------------- */

export interface DecisionPacketPayload {
  portfolio_id?: string;
  strategy_id: string;
  strategy_version?: string;
  weights: Record<string, number>;
  backtest_id?: string;
  research_id?: string;
  risk_check?: Record<string, unknown>;
}

export interface DecisionPacketData {
  data_mode: DataMode;
  packet_id: string;
  portfolio_id: string;
  version: number;
  supersedes_version: number | null;
  strategy_id: string;
  strategy_version: string;
  weights: Record<string, number>;
  backtest_window: { start_date: string; end_date: string };
  data_cutoff: string;
  data_sources: string[];
  metrics: Record<string, number>;
  cost_assumptions: Record<string, unknown>;
  risk_check: Record<string, unknown>;
  ai_research: Record<string, unknown>;
  ai_risk_review: Record<string, unknown>;
  model_call_ids: string[];
  evidence_ids: string[];
  content_hash: string;
  created_at: string;
  research_only: true;
}

/* ---------------- risk report ---------------- */

export interface RiskReportData {
  data_mode: DataMode;
  backtest_id: string;
  engine: string;
  metrics: V2Metrics;
  /** 年 -> 月(01..12) 收益率矩阵（小数，null=当月无数据）。 */
  monthly_returns: Record<string, Record<string, number | null>>;
  rolling: Record<string, number>;
  worst_days: { date: string; daily_return: number }[];
  html_available: boolean;
}

/** 信封便捷别名（与 v1 EnvelopeFields 相同字段集）。 */
export type V2EnvelopeOf<T> = WealthEnvelope<T> & EnvelopeFields;
