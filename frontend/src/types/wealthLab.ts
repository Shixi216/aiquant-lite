/**
 * 1:1 镜像 docs/contracts/wealth-lab-api-v1.yaml（v1 已冻结 2026-10-24）。
 * 冻结条款：任何字段名、类型、枚举值与语义不得单方面修改；
 * 后端契约升版（v1.1/v2）时，必须先同步本文件再联调。
 */

export type DataMode = "REAL" | "DEMO" | "UNAVAILABLE";
export type EnvelopeStatus = "SUCCESS" | "DEGRADED" | "FAILED";

/** 项目统一信封（UnifiedResponse）字段，wealth-lab 在 data 内额外携带 data_mode。 */
export interface EnvelopeFields {
  request_id: string;
  status: EnvelopeStatus;
  error_code: string | null;
  sanitized_error: string | null;
  warnings: string[];
  risk_flags: string[];
  data_cutoff: string | null;
  generated_at: string;
  api_version: string;
  research_only: true;
}

export interface WealthEnvelope<T> extends EnvelopeFields {
  data: T;
}

/* ---------------- meta ---------------- */

export interface DataPoolStatus {
  database: string;
  row_count: number;
  first_date: string | null;
  last_date: string | null;
  sources: string[];
}

export interface MetaData {
  data_mode: DataMode;
  contract_version: "wealth-lab/v1";
  engine_version: string;
  pool: DataPoolStatus;
}

/* ---------------- assets ---------------- */

export type AssetCategory = "EQUITY_ETF" | "BOND_ETF" | "GOLD_ETF" | "CONVERTIBLE_ETF";

export interface Asset {
  symbol: string;
  name: string;
  category: AssetCategory;
  default_weight: number;
  listing_date: string;
  data_first_date: string | null;
  data_last_date: string | null;
  data_rows: number;
  data_status: DataMode;
}

export interface AssetsData {
  data_mode: DataMode;
  assets: Asset[];
}

/* ---------------- backtest ---------------- */

export type RebalanceFrequency = "monthly" | "quarterly" | "none";
export type BenchmarkKey = "EQUAL_WEIGHT" | "510300.SH";

export interface BacktestParams {
  /** 资产权重（小数），和必须为 1。 */
  weights: Record<string, number>;
  start_date: string;
  end_date: string;
  rebalance: RebalanceFrequency;
  /** 单边交易成本（基点），按换手率扣减。 */
  cost_bps: number;
  initial_capital: number;
  benchmarks: BenchmarkKey[];
  /** 数据不足时是否返回 DEMO 合成结果（默认拒绝并返回 UNAVAILABLE）。 */
  allow_demo: boolean;
}

/** 回测表单可编辑参数子集（weights / benchmarks / allow_demo 由页面统一控制）。 */
export type BacktestFormParams = Pick<
  BacktestParams,
  "start_date" | "end_date" | "rebalance" | "cost_bps" | "initial_capital"
>;

/** 等长序列：日期数组与各序列数组一一对应。 */
export interface BacktestSeries {
  dates: string[];
  /** 组合净值（首日 = 1.0）。 */
  portfolio_nav: number[];
  /** 回撤（负小数）。 */
  portfolio_drawdown: number[];
  /** 主基准（EQUAL_WEIGHT）净值。 */
  benchmark_nav: number[];
  /** DEMO 模式必须为 true。 */
  demo: boolean;
}

export interface RebalanceTrade {
  symbol: string;
  weight_before: number;
  weight_after: number;
  /** 单边换手（小数）。 */
  turnover: number;
}

export interface RebalanceEvent {
  date: string;
  trades: RebalanceTrade[];
  /** 本次调仓成本（元）。 */
  cost: number;
}

/** 收益率/波动等一律小数；sharpe rf=2%/年。 */
export interface Metrics {
  total_return: number;
  annual_return: number;
  annual_volatility: number;
  sharpe_ratio: number;
  max_drawdown: number;
  calmar_ratio: number;
  monthly_win_rate: number;
  trading_days: number;
}

export interface BenchmarkResult {
  benchmark: string;
  metrics: Metrics;
}

export interface DataQuality {
  /** 是否后复权净值（fund_adj 因子）。 */
  adjusted: boolean;
  sources: string[];
  /** 四资产共同交易日缺口数。 */
  missing_days: number;
  caveats: string[];
}

export interface BacktestData {
  data_mode: DataMode;
  backtest_id: string;
  params: BacktestParams;
  series: BacktestSeries;
  rebalances: RebalanceEvent[];
  metrics: Metrics;
  benchmarks: BenchmarkResult[];
  data_quality: DataQuality;
}

/* ---------------- risk ---------------- */

export interface RiskRolling {
  dates: string[];
  volatility_63d: number[];
  sharpe_252d: number[];
  drawdown: number[];
}

export interface WorstDay {
  date: string;
  daily_return: number;
}

export interface RiskData {
  data_mode: DataMode;
  backtest_id: string;
  metrics: Metrics;
  rolling: RiskRolling;
  worst_days: WorstDay[];
  /** 1日 VaR95（历史法，负小数）。 */
  value_at_risk_95_1d: number;
}

/* ---------------- stress test ---------------- */

export type StressScenario =
  | { kind: "historical"; label: string; start_date: string; end_date: string }
  | { kind: "synthetic"; label: string; shocks: Record<string, number> };

export interface StressTestPayload {
  scenario: StressScenario;
  weights?: Record<string, number>;
}

export interface StressNavPoint {
  date: string;
  nav: number;
}

export interface AssetContribution {
  symbol: string;
  weight: number;
  shock_return: number;
  contribution: number;
}

export interface StressTestData {
  data_mode: DataMode;
  scenario_label: string;
  portfolio_shock_return: number;
  portfolio_nav_path: StressNavPoint[];
  asset_contributions: AssetContribution[];
}
