/**
 * 1:1 镜像 docs/contracts/wealth-lab-api-v3.yaml（中国多资产研究池）。
 * v1/v2 契约冻结不动；v3 只新增 /v1/wealth-lab/v3/*。
 * v3.2 增量（后端在途/已交付均可选返回，前端字段存在才展示）：
 * - AssetQuality.source_breakdown 统一为 object（历史字符串由 parseSourceBreakdown 双兼容）；
 * - quality 增加 status / q 服务端筛选参数；
 * - PIT 拒绝响应带 requested_start_date / suggested_start_date / excluded_reasons；
 * - 回测结果带 time_semantics 三日期与 pit_rebalance_snapshots.rebalance_records 逐调仓明细。
 */

import type { DataMode, WealthEnvelope } from "./wealthLab";

export type { DataMode, WealthEnvelope };

/** 数据质量状态：合格 / 降级 / 拒绝（判定以后端为准，前端不自行推断）。 */
export type QualityStatus = "QUALIFIED" | "DEGRADED" | "REJECTED";

/* ---------------- universes ---------------- */

export interface UniverseInfo {
  universe_id: string;
  universe_version: string;
  name: string;
  description: string;
  /** 幸存者偏差声明（契约要求如实展示）。 */
  survivorship_bias_note: string;
  created_at: string;
  members: number;
}

export interface UniversesData {
  data_mode: DataMode;
  universes: UniverseInfo[];
}

/* ---------------- universe assets ---------------- */

export interface AssetQuality {
  symbol: string;
  name: string;
  category: string;
  exchange: string;
  list_date: string | null;
  delist_date: string | null;
  data_first: string | null;
  data_last: string | null;
  valid_days: number;
  expected_days: number;
  /** 缺失率（小数），分母为池内真实交易日历。 */
  missing_ratio: number;
  /** 复权因子缺失数（不填充）。 */
  adj_missing: number;
  /** 20日日均成交额（元）。 */
  avg_amount_20d_cny: number;
  source_breakdown: object | string;
  quality_status: QualityStatus;
  notes: string;
  updated_at: string;
  /* ----- v3.1 分类修正维度（后端可选返回） ----- */
  /** 产品形式：ETF / LOF …（如 162411 华宝油气为 QDII-LOF）。 */
  product_form?: string;
  /** 底层资产：如 OVERSEAS_EQUITY（海外权益）。 */
  underlying?: string;
  /** 投资市场：如 HK_CONNECT（港股通）/ OVERSEAS / DOMESTIC。 */
  market_scope?: string;
  /** 汇率风险：如 HKD / USD_MULTI。 */
  currency_risk?: string | null;
}

export interface UniverseAssetsData {
  data_mode: DataMode;
  universe_id: string;
  assets: AssetQuality[];
  members: number;
}

/* ---------------- quality（v3.1 分页） ---------------- */

export interface QualityData {
  data_mode: DataMode;
  total: number;
  quality_counts: Partial<Record<QualityStatus, number>>;
  /** v3.1 分页字段：当前页（1 起）、每页条数、下一页页码（null=末页）。 */
  page?: number;
  limit?: number;
  next_page?: number | null;
  assets: AssetQuality[];
  notes: string[];
}

/* ---------------- backtest eligible（PIT） ---------------- */

export interface EligibilityItem {
  symbol: string;
  eligible: boolean;
  /** 不符合条件的具体原因（后端判定，前端不推断）。 */
  reasons: string[];
  /** 截至 asof 的历史天数。 */
  history_days_at_asof: number;
}

export interface EligibleData {
  data_mode: DataMode;
  universe_id: string;
  asof: string;
  eligible_count: number;
  total: number;
  survivorship_note: string;
  eligibility: EligibilityItem[];
}

/* ---------------- universe backtest ---------------- */

export interface UniverseBacktestPayload {
  universe_id: string;
  start_date?: string;
  end_date?: string;
  cost_bps?: number;
  params_override?: Record<string, unknown>;
}

export interface V3Metrics {
  total_return: number;
  annual_return: number;
  annual_volatility: number;
  sharpe_ratio: number;
  max_drawdown: number;
  calmar_ratio: number;
  monthly_win_rate: number;
  trading_days: number;
  sortino_ratio?: number;
  avg_turnover_per_rebalance?: number;
}

export interface V3WeightSnapshot {
  date: string;
  decision_data_through: string;
  weights: Record<string, number>;
}

/** 单个 PIT 调仓快照点（v3.1：eligible_count+excluded；v3.2 起复用 RebalanceRecord 结构）。 */
export interface PitSnapshotPoint {
  decision_date: string;
  eligible_count?: number;
  /** 被排除资产 → 原因列表（v3.1 键名）。 */
  excluded?: Record<string, string[]>;
  /* ---- v3.2 起后端可能返回 RebalanceRecord 同构字段 ---- */
  eligible_assets?: string[];
  excluded_reasons?: Record<string, string[]>;
  decision_data_through?: string;
  selected_assets?: string[];
  target_weights?: Record<string, number>;
  cash_weight?: number;
  cost?: number;
}

/** v3.2 逐调仓完整记录（后端可选返回；存在时审计区渲染逐行表 + 合格数折线）。 */
export interface RebalanceRecord {
  decision_date: string;
  /** 该次决策可用的最新数据截止日（T-1 口径）。 */
  decision_data_through: string;
  /** PIT 判定合格的资产（后端排序）。 */
  eligible_assets: string[];
  /** 实际入选（非现金）资产。 */
  selected_assets: string[];
  /** 目标权重（symbol → 权重）。 */
  target_weights: Record<string, number>;
  /** 现金仓位（1 - Σ权重）。 */
  cash_weight: number;
  cost: number;
  /** 被排除资产 → 原因列表（v3.2 键名；个别版本可能缺省）。 */
  excluded_reasons?: Record<string, string[]>;
}

/** v3.2 时间语义：消除"回测起点"歧义（请求起点 ≠ 实际首个交易日 ≠ 首次建仓日）。 */
export interface TimeSemantics {
  requested_start_date: string;
  effective_start_date: string;
  first_investment_date: string | null;
  end_date: string;
  actual_trading_days: number;
  start_shift_reason: string | null;
  note: string;
}

/** v3.1 逐调仓 PIT 审计（dict 结构：note/rebalances 计数/first/last 端点/min-max）。 */
export interface PitRebalanceSnapshots {
  note: string;
  rebalances: number;
  first?: PitSnapshotPoint;
  last?: PitSnapshotPoint;
  eligible_count_min: number;
  eligible_count_max: number;
  /** v3.2：逐调仓完整记录（后端增量，旧后端无此字段）。 */
  rebalance_records?: RebalanceRecord[];
}

/** v3 池回测结果：series/metrics/weight_history 与 v2 同构，另带 common_window 与 PIT 门控快照。 */
export interface UniverseBacktestData {
  data_mode: DataMode;
  backtest_id: string;
  universe_id: string;
  strategy: {
    strategy_id: string;
    strategy_version: string;
    name: string;
    description: string;
    params: Record<string, unknown>;
    min_history_days: number;
    rebalance: string;
    risk_notes: string;
    asset_selection: string;
  };
  common_window: {
    start_date: string;
    end_date: string;
    trading_days: number;
  };
  /** v3.2：逐调仓完整记录（后端可能放在顶层或嵌在 pit_rebalance_snapshots 内）。 */
  rebalance_records?: RebalanceRecord[];
  pit_gate: {
    asof: string;
    eligible: string[];
    excluded: Record<string, unknown>;
    note: string;
  };
  /** v3.1：每个调仓决策时点重新判定资产资格的审计快照。 */
  pit_rebalance_snapshots?: PitRebalanceSnapshots;
  /** v3.2：时间语义（requested/effective/first_investment 三日期）。 */
  time_semantics?: TimeSemantics;
  series: {
    dates: string[];
    nav: number[];
    drawdown: number[];
    cash_weight: number[];
    demo: boolean;
  };
  metrics: V3Metrics;
  weight_history: V3WeightSnapshot[];
  rebalances: { date: string; cost: number }[];
}
