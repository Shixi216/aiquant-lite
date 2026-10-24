/**
 * 唯一中文语义层：所有 V2 页面的文案、格式化与色彩令牌都从这里取。
 * 全部为全函数（永不返回 undefined）；禁止页面内散落三元翻译。
 */

export const UP_COLOR = "#f04848"; // A股红涨
export const DOWN_COLOR = "#2ebd85"; // A股绿跌
export const NEUTRAL_COLOR = "#8494a7";
export const NO_DATA = "暂无数据";

const COMPONENT_NAMES: Record<string, string> = {
  router: "数据服务",
  data_hub: "数据中心",
  jinniu: "金牛数据",
  tushare: "Tushare",
  baostock: "BaoStock",
  coverage: "数据覆盖",
  database: "主数据库",
  migrations: "数据库结构版本",
  finance_mcp: "金融工具服务",
  scanner: "选股扫描",
  formal_evaluation: "正式评估",
  fundamental_coverage: "基本面覆盖",
  sentiment_coverage: "情绪数据覆盖",
  policy_news_coverage: "政策新闻覆盖",
  capital_flow_coverage: "资金流覆盖",
  wecom: "企业微信通道",
  live_trading: "实盘执行接口",
};
export function getComponentLabel(name: string): string {
  return COMPONENT_NAMES[name] ?? name;
}

export const DECISION_ACTION_LABELS: Record<string, string> = {
  STRONG_BUY: "强烈买入",
  BUY: "买入",
  SMALL_BUY: "小仓买入",
  WAIT: "等待",
  AVOID: "回避",
  ADD: "加仓",
  HOLD: "持有",
  REDUCE: "减仓",
  SELL: "卖出",
  TAKE_PROFIT: "止盈",
  STOP_LOSS: "止损",
  EXIT: "退出",
  CLEAR: "清仓",
};

export const SCANNER_BUCKET_LABELS: Record<string, string> = {
  CORE: "核心候选",
  NEAR: "临界候选",
  CONTROL: "对照样本",
  unavailable: "尚无正式扫描层级",
};

export const CAPITAL_BEHAVIOR_LABELS: Record<string, string> = {
  NORMAL: "正常状态",
  ACCUMULATION_CANDIDATE: "吸筹候选",
  TESTING_CANDIDATE: "试盘候选",
  WASHOUT_CANDIDATE: "洗盘候选",
  MARKUP: "拉升阶段",
  LATE_MARKUP: "拉升后期",
  DISTRIBUTION_CANDIDATE: "派发候选",
  BREAKOUT_CONFIRM: "突破确认",
  BREAKOUT_FAILURE: "突破失败",
  CAPITULATION: "恐慌出清",
  REPAIR: "趋势修复",
  UNCERTAIN: "不确定",
  CHASE_RISK: "追涨风险",
};

export const CAPITAL_BEHAVIOR_EXPLAIN: Record<string, string> = {
  MARKUP: "拉升阶段：价格与成交活跃度同步增强，当前属于趋势推进阶段。",
  LATE_MARKUP: "拉升后期：趋势仍强，但位置已经偏高，新开仓风险上升。",
  ACCUMULATION_CANDIDATE: "吸筹候选：出现低位承接迹象，趋势尚未确认。",
  DISTRIBUTION_CANDIDATE: "派发候选：高位放量滞涨，注意兑现风险。",
};

export const HEALTH_STATUS_LABELS: Record<string, string> = {
  HEALTHY: "正常",
  healthy: "正常",
  ok: "正常",
  OK: "正常",
  DEGRADED: "降级",
  degraded: "降级",
  stale: "数据过期",
  ready: "已就绪",
  breaker_open: "熔断开启",
  open: "熔断开启",
  half_open: "半开恢复",
  closed: "熔断关闭",
  unreachable: "不可达",
  not_deployed: "接口未部署",
  unknown: "未知状态",
  NOT_READY: "尚未就绪",
  not_ready: "尚未就绪",
  UNAVAILABLE: "暂不可用",
  unavailable: "暂不可用",
  FAILED: "失败",
  ERROR: "异常",
};

export const EXECUTION_STATUS_KNOWN: Record<string, string> = {
  立即执行: "立即执行",
  等待回调: "等待回调",
  等待止跌确认: "等待止跌确认",
  禁止追高: "禁止追高",
  信号失效: "信号失效",
  已失效: "已失效",
  等待确认: "等待确认",
};

export const RISK_LABELS: Record<string, string> = {
  data_incomplete: "部分风险依据不可用，评估不完整",
  decision_not_generated: "尚未生成个股决策记录",
  research_finding: "本轮研究提示",
  decision_unavailable: "决策记录暂时读取失败",
  score_unavailable: "四维评分暂不可用",
  veto_unchecked: "尚无可核查的 VETO 结果",
  security_status_unavailable: "证券状态暂时读取失败",
  security_status_unknown: "证券上市状态尚未确认",
  st_risk: "存在 ST 风险警示",
  security_suspended: "证券当前处于停牌状态",
  PARTIAL_UNIVERSE: "样本不完整",
  partial_universe: "市场宽度基于不完整样本",
  zone_expired: "交易计划已过期",
  veto_triggered: "硬性风险拦截",
  stale_data: "数据已过期",
  CHASE_RISK_HIGH: "当前追涨风险较高",
  adjustment_fallback: "复权口径降级",
  legacy_score: "评分来自降级估算（非正式截面）",
  security_not_active: "证券当前不是正常上市状态",
  NO_RISK: "现有记录未见风险标记",
  high_volatility: "高波动风险",
  low_liquidity: "流动性不足",
  overbought: "超买风险",
  oversold: "超卖风险",
  fundamental_weak: "基本面偏弱",
};

export function getRiskDetail(code: string, value: unknown): string | null {
  if (code === "research_finding") return typeof value === "string" ? value : null;
  if (code === "decision_not_generated") {
    return "VETO 拦截与交易区间尚未完成复核，请先完成个股分析并保存决策。";
  }
  if (code === "veto_unchecked") {
    return "现有决策未包含明确的风控检查结果，不能视为通过检查。";
  }
  if (["decision_unavailable", "score_unavailable", "security_status_unavailable"].includes(code)) {
    return getBlockReasonLabel(typeof value === "string" ? value : "upstream_error");
  }
  if (code === "veto_triggered" && value && typeof value === "object") {
    const reason = (value as Record<string, unknown>).veto_reason;
    return typeof reason === "string" && reason.trim() ? reason : null;
  }
  return null;
}

export const SCORE_SOURCE_LABELS: Record<string, string> = {
  cross_section_4d: "四维横截面评分",
  legacy_60_40_fallback: "降级估算（技术60%+基本40%）",
  unavailable: "数据不可用",
};

export const BLOCK_REASON_LABELS: Record<string, string> = {
  router_unreachable: "数据服务不可达",
  endpoint_not_deployed: "接口未部署（等待服务重启）",
  upstream_timeout: "上游超时",
  not_found: "暂无数据",
  upstream_error: "数据不可用",
  stale_data: "数据过期",
};

export const DATA_SOURCE_LABELS: Record<string, string> = {
  router_canonical_history: "正式历史行情",
  local_replay_history: "本地历史行情",
  "local_replay_history+provider_tail_fill": "本地历史行情 + 增量行情",
  provider_tail_fill: "增量行情",
  local_backup_snapshot: "临时备份行情",
  router_canonical: "正式数据",
  "router.equity_context_overview": "正式市场环境数据",
  "router.cross_section": "正式横截面评分",
  "router.market_breadth": "正式市场宽度数据",
  unavailable: "暂不可用",
};

export const FORMAL_STATUS_LABELS: Record<string, string> = {
  formal: "正式",
  auxiliary: "辅助指标",
  fact: "事实数据",
  temporary_fallback: "临时备份行情",
  unavailable: "暂不可用",
};

export const ADJUSTMENT_LABELS: Record<string, string> = {
  FORWARD_ADJUSTED: "前复权",
  RAW: "不复权",
  BACKWARD_ADJUSTED: "后复权",
};

export const TIME_SEMANTIC_LABELS: Record<string, string> = {
  market_trade_date: "行情交易日",
  market_snapshot_cutoff: "行情快照时间",
  score_data_cutoff: "四维评分截至",
  score_generated_at: "四维评分生成时间",
  coverage_report_date: "覆盖报告截至",
};

export const DECISION_STATUS_LABELS: Record<string, string> = {
  ACTIVE: "当前有效",
  CURRENT: "当前有效",
  SUPERSEDED: "已替代",
  DRAFT: "草稿",
  PENDING_CONFIRMATION: "待确认",
  CONFIRMED: "已确认",
  PARTIALLY_EXECUTED: "部分执行",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
  EXPIRED: "已过期",
};

export const HOLDING_HORIZON_LABELS: Record<string, string> = {
  INTRADAY: "日内",
  T_PLUS_1: "次日",
  SHORT_TERM: "短期（1-3日）",
  SWING: "波段（1-2周）",
  MEDIUM_TERM: "中期（1-3月）",
  LONG_TERM: "长期",
};

export const VETO_STATUS_LABELS: Record<string, string> = {
  NO_VETO: "无否决",
  VETO_TRIGGERED: "触发否决",
  VETO: "风控否决",
  PASSED: "通过",
};

export const STRATEGY_ROLE_LABELS: Record<string, string> = {
  BASELINE: "基准策略",
  CHALLENGER: "挑战候选",
  EXPLORATORY: "探索候选",
  BASE: "基准",
};

export const VERIFICATION_STATUS_LABELS: Record<string, string> = {
  SINGLE_SOURCE: "单一来源",
  VERIFIED: "已核验",
  UNVERIFIED: "未核验",
  CANONICAL: "正式收录",
  PENDING: "待核验",
};

export const DATA_TYPE_LABELS: Record<string, string> = {
  "financial_statement:income": "利润表",
  "financial_statement:balancesheet": "资产负债表",
  "financial_statement:cashflow": "现金流量表",
  FINANCIAL_INDICATOR: "主要财务指标",
  INCOME: "利润表",
  BALANCESHEET: "资产负债表",
  CASHFLOW: "现金流量表",
};

export const GATE_STATUS_LABELS: Record<string, string> = {
  NO_PRODUCTION_CANDIDATE: "未产生实盘候选",
  GATE_PASSED: "准入通过",
  GATE_REJECTED: "准入未通过",
};

export const ALPHA_CONCLUSION_LABELS: Record<string, string> = {
  ACCEPTED: "已采纳",
  REJECTED: "未通过",
  INCONCLUSIVE: "不显著",
  PENDING: "评估中",
};

export const TIMEPOINT_SHORT_LABELS: Record<string, string> = {
  pre: "盘前",
  bid: "竞价",
  v1: "早盘1",
  v2: "早盘2",
  v3: "早盘3",
  c10: "10:00",
  c1030: "10:30",
  am: "午间",
  pm: "下午开盘",
  pm14: "14:00",
  pm1430: "14:30",
  vfinal: "尾盘",
  close: "收盘",
  review: "复盘",
};

export const RANK_TOOLTIP =
  "表示该股票在当前市场截面中的相对位置，不是上涨概率。";

export const LEGACY_NOTE = "LEGACY：降级估算，非正式截面评分";

function fromDict(dict: Record<string, string>, key: string | null | undefined, fallbackPrefix: string): string {
  if (key === null || key === undefined) return NO_DATA;
  const value = dict[key];
  if (value !== undefined) return value;
  return `${fallbackPrefix}（${key}）`;
}

export function getDecisionLabel(action: string | null | undefined, actionZh?: string | null): string {
  if (actionZh && actionZh.trim()) return actionZh;
  return fromDict(DECISION_ACTION_LABELS, action, "未知动作");
}

export function getScannerBucketLabel(bucket: string | null | undefined): string {
  if (bucket === null || bucket === undefined) return SCANNER_BUCKET_LABELS.unavailable;
  return fromDict(SCANNER_BUCKET_LABELS, bucket, "未知层级");
}

export function getCapitalBehaviorLabel(code: string | null | undefined): string {
  return fromDict(CAPITAL_BEHAVIOR_LABELS, code, "未知状态");
}

export function getHealthStatusLabel(status: string | null | undefined): string {
  if (status === null || status === undefined) return NO_DATA;
  return fromDict(HEALTH_STATUS_LABELS, status, "未知状态");
}

export function getExecutionStatusLabel(status: string | null | undefined): string {
  if (status === null || status === undefined) return NO_DATA;
  const known = EXECUTION_STATUS_KNOWN[status];
  if (known) return known;
  if (/[\u4e00-\u9fff]/.test(status)) return status;
  return "未知状态";
}

export function getRiskLabel(code: string | null | undefined): string {
  return fromDict(RISK_LABELS, code, "未知风险");
}

export function getScoreSourceLabel(source: string | null | undefined): string {
  return fromDict(SCORE_SOURCE_LABELS, source, "未知来源");
}

export function getBlockReasonLabel(code: string | null | undefined): string {
  return fromDict(BLOCK_REASON_LABELS, code, "暂无数据");
}

export function getDataSourceLabel(source: string | null | undefined): string {
  return fromDict(DATA_SOURCE_LABELS, source, "未知来源");
}

export function getFormalStatusLabel(status: string | null | undefined): string {
  return fromDict(FORMAL_STATUS_LABELS, status, "未知性质");
}

export function getAdjustmentLabel(type: string | null | undefined): string {
  return fromDict(ADJUSTMENT_LABELS, type, "未知口径");
}

export function getTimeSemanticLabel(key: string | null | undefined): string {
  return fromDict(TIME_SEMANTIC_LABELS, key, "未知时间字段");
}

export function getDecisionStatusLabel(status: string | null | undefined): string {
  return fromDict(DECISION_STATUS_LABELS, status, "未知状态");
}

export function getHoldingHorizonLabel(horizon: string | null | undefined): string {
  if (horizon === null || horizon === undefined) return NO_DATA;
  return fromDict(HOLDING_HORIZON_LABELS, horizon, horizon);
}

export function getVetoStatusLabel(status: string | null | undefined): string {
  if (status === null || status === undefined) return NO_DATA;
  return fromDict(VETO_STATUS_LABELS, status, status);
}

export function getStrategyRoleLabel(role: string | null | undefined): string {
  if (role === null || role === undefined) return NO_DATA;
  return fromDict(STRATEGY_ROLE_LABELS, role, role);
}

export function getVerificationStatusLabel(status: string | null | undefined): string {
  if (status === null || status === undefined) return NO_DATA;
  return fromDict(VERIFICATION_STATUS_LABELS, status, status);
}

export function getDataTypeLabel(type: string | null | undefined): string {
  if (type === null || type === undefined) return NO_DATA;
  return fromDict(DATA_TYPE_LABELS, type, type);
}

export function getGateStatusLabel(status: string | null | undefined): string {
  if (status === null || status === undefined) return NO_DATA;
  return fromDict(GATE_STATUS_LABELS, status, status);
}

export function getAlphaConclusionLabel(conclusion: string | null | undefined): string {
  if (conclusion === null || conclusion === undefined) return NO_DATA;
  return fromDict(ALPHA_CONCLUSION_LABELS, conclusion, conclusion);
}

export function getTimepointLabel(key: string | null | undefined): string {
  if (key === null || key === undefined) return NO_DATA;
  return fromDict(TIMEPOINT_SHORT_LABELS, key, key);
}

// ---------------------------------------------------------------------------
// 格式化器
// ---------------------------------------------------------------------------

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

// Production rank and composite values are centered in [-1, 1].
// Only rank fields are percentiles; composite is a weighted score.
export function formatScore(value: unknown): string {
  if (!finite(value)) return NO_DATA;
  return ((value + 1) * 50).toFixed(1);
}

export function formatRank(value: number | null | undefined): string {
  if (!finite(value)) return NO_DATA;
  return `${Math.round((value + 1) * 50)}百分位`;
}

export function formatRankTop(value: number | null | undefined): string {
  if (!finite(value)) return NO_DATA;
  const pct = Math.max(1, Math.round((1 - value) * 50));
  return `市场前${pct}%`;
}

export function formatPct(value: number | null | undefined, digits = 2): string {
  if (!finite(value)) return NO_DATA;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatVolume(value: number | null | undefined): string {
  if (!finite(value)) return NO_DATA;
  value /= 100; // Canonical chart volume is shares; display A-share lots.
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿手`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(2)}万手`;
  return `${value.toFixed(0)}手`;
}

export function formatAmount(value: number | null | undefined): string {
  if (!finite(value)) return NO_DATA;
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿元`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(2)}万元`;
  return `${value.toFixed(0)}元`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return NO_DATA;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  // Never let a browser invent the timezone of a naive timestamp.
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
    return /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(value)
      ? `${value.replace("T", " ").slice(0, 16)}（时区未确认）` : value;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const parts = Object.fromEntries(new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(parsed).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return NO_DATA;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  return formatDateTime(value).slice(0, 10);
}

/** Confidence is a probability in [0, 1], not a centered [-1, 1] score. */
export function formatConfidence(value: unknown): string {
  if (!finite(value) || value < 0 || value > 1) return NO_DATA;
  return `${(value * 100).toFixed(1)}%`;
}

/** Fiscal period comes from report metadata, never an announcement/event timestamp. */
export function getFinancialReportPeriod(metrics: unknown): string {
  if (!metrics || typeof metrics !== "object" || Array.isArray(metrics)) return NO_DATA;
  const record = metrics as Record<string, unknown>;
  const periods: string[] = [];
  for (const key of ["report_period", "end_date"]) {
    const raw = record[key];
    if (typeof raw !== "string") continue;
    const value = /^\d{8}$/.test(raw) ? `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6)}` : raw;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) continue;
    const parsed = new Date(`${value}T00:00:00Z`);
    if (Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value) periods.push(value);
  }
  return new Set(periods).size > 1 ? "报告期字段冲突" : periods[0] ?? NO_DATA;
}

export function hasInconsistentTurnover(bar: { volume?: unknown; amount?: unknown }): boolean {
  return (finite(bar.volume) && bar.volume < 0) || (finite(bar.amount) && bar.amount < 0)
    || (bar.volume === 0 && finite(bar.amount) && bar.amount > 0);
}

export function getWalkForwardLabel(row: { train_window_days: number; fold: number }): string {
  return `${row.train_window_days}日 · 第${row.fold + 1}折`;
}

export function getCandidateLabel(id: string): string {
  const families: Record<string, string> = {
    legacy: "传统基准", static: "静态组合", dynamic: "动态组合", dynamic_risk: "动态风控",
    risk_overlay: "风险叠加", regime: "市场状态", interaction: "交互组合",
    ablation: "消融实验", ml_ridge: "岭回归模型",
  };
  const match = /^(.*)_([a-f0-9]+)$/.exec(id);
  return match ? `${families[match[1]!] ?? "研究候选"} · ${match[2]!.slice(0, 6)}` : id;
}

/* ---------------- 量化面板 ---------------- */

export const SCAN_TYPE_LABELS: Record<string, string> = {
  ma_bull: "均线多头排列",
  ma_bear: "均线空头排列",
  golden_cross: "MA5金叉MA20",
  dead_cross: "MA5死叉MA20",
  breakout_20d: "突破20日新高",
  volume_surge: "放量上涨(量比≥2)",
};

export function getScanTypeLabel(type: string | null | undefined): string {
  return fromDict(SCAN_TYPE_LABELS, type, "未知扫描");
}

export const QUANT_REASON_LABELS: Record<string, string> = {
  no_local_history: "本地量化数据库暂无数据",
  sidecar_missing: "本地量化数据库不存在",
  quant_read_error: "量化数据读取失败",
  unknown_scan_type: "未知扫描类型",
  latest_session_unavailable: "当日快照暂不可用（数据源未响应）",
};

export function getQuantReasonLabel(code: string | null | undefined): string {
  return fromDict(QUANT_REASON_LABELS, code, "暂无数据");
}

export function formatAmountYi(value: number | null | undefined): string {
  if (!finite(value)) return NO_DATA;
  if (Math.abs(value) >= 1e12) return `${(value / 1e12).toFixed(2)}万亿`;
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(1)}亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return value.toFixed(0);
}
