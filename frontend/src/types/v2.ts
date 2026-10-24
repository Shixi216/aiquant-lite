/** 1:1 镜像 /webui/v2/* 响应结构（后端 webui/schemas_v2.py）。 */

export interface Block<T = unknown> {
  available: boolean;
  reason_code: string | null;
  stale: boolean;
  source: string | null;
  data: T | null;
  formal_status?: string;
}

export interface TimeField {
  value: string | null;
  source: string;
  label_zh: string;
}

export interface ChangeBuckets {
  gt7: number; p5_7: number; p2_5: number; p0_2: number; flat: number;
  n0_2: number; n2_5: number; n5_7: number; lt7: number;
}

export interface MarketBreadthData {
  snapshot_id: string;
  snapshot_time: string;
  data_cutoff: string;
  expected_universe_size: number;
  received_symbol_count: number;
  valid_symbol_count: number;
  coverage_ratio: number;
  completeness_status: string;
  stale: boolean;
  data_age_seconds: number;
  advances: number;
  declines: number;
  flats: number;
  limit_ups: number | null;
  limit_downs: number | null;
  limit_counts_derivation: string;
  total_amount: number | null;
  median_change_pct: number | null;
  change_buckets: ChangeBuckets;
  market_trade_date: string | null;
  market_trade_date_status: string | null;
  market_trade_date_derivation: string;
  snapshot_data_trade_date: string | null;
  bars_max_trade_date: string | null;
  partial_universe: boolean;
}

export interface Thresholds {
  data_cutoff: string;
  p50: number; p75: number; p90: number; p95: number;
  strong_buy_q: number; buy_q: number; n_total: number;
  generated_at: string;
}

export interface DashboardResponse {
  time_semantics: Record<string, TimeField>;
  market_breadth: Block<MarketBreadthData>;
  score_preview: Block;
  coverage: Block;
  components: { name: string; status: string; healthy: boolean }[];
  market_context: Block<MarketContextData>;
  dragon_tiger: Block<DragonTigerBoardData>;
  provenance: Record<string, string | null>;
  fixture_mode: boolean;
}

export interface DragonTigerItem {
  symbol: string;
  name: string | null;
  reasons: string[];
  buy_amount: number | null;
  sell_amount: number | null;
  net_amount: number | null;
  institution_net_amount: number | null;
  seat_count: number | null;
  institution_seat_count: number | null;
}

export interface DragonTigerBoardData {
  trade_date: string;
  available_time: string | null;
  source: string | null;
  quality_flags: string[];
  publication_type: "POST_CLOSE_EOD";
  is_realtime: false;
  total: number;
  items: DragonTigerItem[];
}

export interface CrossSectionItem {
  symbol: string;
  name?: string | null;
  industry?: string | null;
  tech_rank: number | null;
  fund_rank: number | null;
  sent_rank: number | null;
  margin_rank: number | null;
  composite_score: number;
  scanner_bucket?: string | null;
  quote?: { price: number | null; change_pct: number | null; turnover_rate: number | null } | null;
}

export interface OpportunitiesResponse {
  classification_source: string;
  ranking_data_cutoff: string | null;
  thresholds: Thresholds | null;
  total: number;
  offset: number;
  limit: number;
  sort: string;
  items: CrossSectionItem[];
  cross_section_block: Block;
  provenance: Record<string, string | null>;
  fixture_mode: boolean;
}

export interface FrozenZone { low: number; high: number }

export interface DecisionPacketData {
  decision_id: string;
  symbol: string;
  action: string;
  action_zh: string;
  confidence: number;
  formal_score: number;
  enhanced_score: number | null;
  data_cutoff: string;
  created_at: string;
  expires_at: string;
  zone_version: string;
  zone_generated_at: string;
  zone_valid_until: string;
  zone_expired: boolean | null;
  reference_price: number;
  has_frozen_plan: boolean;
  frozen_entry_zone: FrozenZone | null;
  frozen_preferred_zone: FrozenZone | null;
  frozen_stop_loss_price: number | null;
  veto_result: Record<string, unknown> | null;
  evidence_ids: string[];
}

export interface CrossSectionSymbolData {
  symbol: string;
  name?: string | null;
  industry?: string | null;
  score_source: string;
  composite_score: number;
  tech_rank: number | null;
  fund_rank: number | null;
  sent_rank: number | null;
  margin_rank: number | null;
}

export interface StockIntelResponse {
  symbol: string;
  identity: StockIdentity;
  database_context: Block<StockDatabaseContext>;
  decision_card: Block<DecisionPacketData>;
  research_review?: ResearchReview | null;
  four_dimensions: Block<CrossSectionSymbolData>;
  risk: Block<{ risks: { code: string; value: unknown }[] }>;
  provenance: Record<string, string | null>;
  fixture_mode: boolean;
}

export interface ResearchReview {
  symbol: string;
  source: "stock_report_service";
  research_only: true;
  frozen_plan_saved: false;
  generated_at: string;
  price_as_of: string;
  action_zh: string;
  execution_status: string;
  confidence: number;
  risk_messages: string[];
  sha256: string;
}

export interface SectorContext {
  sector: string;
  classification_system: string;
  member_count: number;
  advances: number;
  declines: number;
  advance_ratio: number;
  median_change_pct: number | null;
  total_amount: number | null;
  average_composite_score: number | null;
}

export interface MarketContextData {
  snapshot_id: string;
  data_cutoff: string;
  coverage_ratio: number;
  completeness_status: string;
  valid_symbol_count: number;
  macro: {
    market_regime: string;
    advance_ratio: number | null;
    median_change_pct: number | null;
    total_amount: number | null;
    sentiment?: Record<string, unknown>;
    capital_flow?: Record<string, unknown>;
  };
  sectors: SectorContext[];
  calculation: string;
  source: string;
}

export interface StockIdentity {
  symbol: string;
  code: string;
  name?: string | null;
  company_name?: string | null;
  exchange: string;
  market?: string;
  board?: string;
    list_date?: string | null;
    delist_date?: string | null;
  listing_status?: string;
  is_st?: boolean;
  is_suspended?: boolean;
  source?: string;
  verification_status?: string;
  updated_at?: string;
}

export interface StockSearchItem extends StockIdentity {
  symbol: string;
  name: string;
}

export interface StockSearchResponse {
  query: string;
  items: StockSearchItem[];
  total: number;
}

export interface StockDatabaseContext {
  symbol: string;
  identity: StockIdentity;
  industry: Record<string, unknown> | null;
  quote: Record<string, unknown> | null;
  factors: Array<Record<string, unknown>>;
  financials: Array<Record<string, unknown>>;
  source: string;
  calculation: string;
}

export interface ChartBar {
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  amount: number | null;
  change_pct: number | null;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma60: number | null;
}

export interface ChartResponse {
  symbol: string;
  bars: ChartBar[];
  data_source: string;
  formal_status: string;
  data_time: string | null;
  market_coverage_date: string | null;
  adjustment: {
    requested_adjustment_type: string | null;
    effective_adjustment_type: string | null;
    adjustment_fallback: boolean | null;
    adjustment_fallback_reason: string | null;
    factor_anchor_date: string | null;
  };
  tail_fill: { triggered: boolean; ok: boolean; reason_code: string; detail?: string } | null;
  unavailable_reason: string | null;
  fixture_mode: boolean;
}

/* ---------------- 量化面板（/webui/v2/quant/*） ---------------- */

export interface QuantEnvelope<T> {
  available: boolean;
  reason_code: string | null;
  data_cutoff?: string;
  data_source?: string;
  formal_status?: string;
  data: T | null;
}

export interface QuantSentiment {
  trade_date: string;
  valid_quote_count: number;
  advancers: number;
  decliners: number;
  unchanged: number;
  market_return: number | null;
  market_turnover: number | null;
  advance_ratio: number | null;
  breadth_strength: number | null;
  market_turnover_change: number | null;
  limit_ups: number;
  limit_downs: number;
  broken_boards: number;
  total_amount: number | null;
  median_return: number | null;
}

export interface ConsecutiveBoard {
  symbol: string;
  name: string;
  streak: number;
  close: number | null;
  return_pct: number | null;
  turnover_rate: number | null;
}

export interface QuantOverviewData {
  sentiment: QuantSentiment;
  temperature: number;
  consecutive_boards: ConsecutiveBoard[];
  universe_size: number;
}

export interface BreadthPoint {
  trade_date: string;
  advancers: number;
  decliners: number;
  unchanged: number;
  market_return: number | null;
  market_turnover: number | null;
  advance_ratio: number | null;
  breadth_strength: number | null;
  limit_ups: number | null;
  limit_downs: number | null;
}

export interface MarginPoint {
  trade_date: string;
  margin_total: number | null;
  change_pct: number | null;
}

export interface LimitPoolRow {
  symbol: string;
  name: string;
  close: number | null;
  return_pct: number | null;
  volume_ratio: number | null;
  turnover_rate: number | null;
}

export interface LimitPoolsData {
  trade_date: string;
  limit_up_pool: LimitPoolRow[];
  limit_down_pool: LimitPoolRow[];
}

export interface DragonTigerItem {
  symbol: string;
  name: string;
  buy_amount: number | null;
  sell_amount: number | null;
  net_amount: number | null;
  institution_net_amount: number | null;
  seat_count: number | null;
  institution_seat_count: number | null;
  reasons: string[];
}

export interface ScanItem {
  symbol: string;
  name: string;
  close: number | null;
  return_pct: number | null;
  volume_ratio: number | null;
  turnover_rate: number | null;
  amount: number | null;
  ma20_dev_pct: number | null;
  ma60_dev_pct: number | null;
}

export interface ScanData {
  scan_type: string;
  trade_date: string;
  total: number;
  items: ScanItem[];
}

export interface RpsItem {
  symbol: string;
  name: string;
  ret60_pct: number | null;
  rps: number | null;
  close: number | null;
  return_pct: number | null;
}

export interface RpsData {
  trade_date: string;
  days: number;
  min_rps: number;
  items: RpsItem[];
}

export interface LatestSessionStock {
  symbol: string;
  name: string | null;
  close: number | null;
  return_pct: number | null;
  amount: number | null;
}

export interface LatestSessionData {
  trade_date: string;
  advancers: number;
  decliners: number;
  flats: number;
  limit_ups_approx: number;
  limit_downs_approx: number;
  total_amount: number | null;
  gainers: LatestSessionStock[];
  losers: LatestSessionStock[];
  top_amount: LatestSessionStock[];
  note: string;
}
