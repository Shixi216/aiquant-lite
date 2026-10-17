import { api } from "./client";

export interface DecisionScenario {
  summary: string;
  conditions: string[];
}

export interface DecisionTraceLite {
  final_action: string;
  final_target_weight: number;
  rationale_summary: string[];
  evidence_refs: string[];
}

export interface DecisionPacket {
  decision_id: string;
  decision_version: number;
  supersedes_version: number | null;
  symbol: string;
  generated_at: string;
  status: string;
  decision: DecisionTraceLite;
  confidence: number;
  evidence_quality: number;
  bull_case: DecisionScenario;
  base_case: DecisionScenario;
  bear_case: DecisionScenario;
  entry_conditions: string[];
  invalidation_conditions: string[];
  risk_flags: string[];
  suggested_position_limit: number;
  holding_horizon: string;
  assumptions: string[];
  missing_information: string[];
}

export interface DailyReview {
  review_date: string;
  veto_count: number;
  summary: string[];
  decision_packets: DecisionPacket[];
  model_errors: string[];
  evidence_gaps: string[];
  data_quality_issues: string[];
}

export interface TradePlan {
  plan_id: string;
  local_user_id: string;
  symbol: string;
  stock_name: string;
  action: string;
  current_position: number;
  recommended_position: number;
  position_change: number;
  current_price: number;
  entry_zone: number[];
  add_zone: number[];
  reduce_zone: number[];
  stop_loss_price: number | null;
  stop_loss_condition: string;
  take_profit_zone: number[];
  invalidation_condition: string;
  expected_holding_period: string;
  risk_reward_ratio: number;
  formal_score: number;
  confidence: number;
  data_status: string;
  veto_status: string;
  supporting_evidence: string[];
  major_risks: string[];
  status: string;
  status_zh: string;
  created_at: string;
  expires_at: string | null;
}

export async function fetchDailyReview(date: string): Promise<DailyReview> {
  const { data } = await api.get(`/v1/reviews/daily/${date}`);
  return data;
}

export async function fetchDecisionVersions(decisionId: string): Promise<DecisionPacket[]> {
  const { data } = await api.get(`/v1/decisions/${decisionId}/versions`);
  return data;
}

export async function fetchTradePlans(params: {
  local_user_id: string;
  status?: string;
}): Promise<TradePlan[]> {
  const { data } = await api.get("/v1/trade-plans", { params });
  return data;
}
