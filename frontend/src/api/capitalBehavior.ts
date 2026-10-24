import { bff } from "./client";

export interface RunManifest {
  run_at: string;
  research_only: boolean;
  creates_trade: boolean;
  data_range: { start: string; end: string };
  counts: {
    packets: number;
    states: number;
    strategies: number;
    similarity_queries: number;
  };
  decision: string;
}

export interface CandidateMetrics {
  annualized_return: number;
  sharpe: number;
  max_drawdown: number;
  calmar: number;
  tail_loss: number;
  year_2026_return?: number;
}

export interface Baseline {
  candidate_id: string;
  status: string;
  cost_bp: number;
  metrics: CandidateMetrics;
}

export interface ResearchCandidate {
  candidate_id: string;
  role: string;
  parameters: Record<string, number>;
  cost_bp: number;
  metrics: CandidateMetrics;
  promotion_passed: boolean;
  failed_rules: string[];
}

export interface CandidatesPayload {
  baseline: Baseline;
  research_candidates: ResearchCandidate[];
  production_candidate: unknown;
}

export interface StateDefinition {
  description: string;
  intercept: number;
  weights: Record<string, number>;
}

export interface StateDefinitions {
  definition_version: string;
  frozen_at: string;
  states: Record<string, StateDefinition>;
  lifecycle_hypotheses: [string, string][];
  risk_states: string[];
  alpha_states: string[];
}

export interface TransitionRow {
  from_state: string;
  to_state: string;
  transitions: number;
  source_dates: number;
  mean_return_1d: number | null;
  mean_return_5d: number | null;
  mean_return_20d: number | null;
  win_rate_20d: number | null;
  persistence: number | null;
  lifecycle_hypothesis: boolean;
  transition_probability: number;
}

export interface CsvTable {
  columns: string[];
  rows: TransitionRow[];
}

export async function fetchManifest(): Promise<RunManifest> {
  const { data } = await bff.get("/capital-behavior/manifest");
  return data;
}

export async function fetchStates(): Promise<StateDefinitions> {
  const { data } = await bff.get("/capital-behavior/states");
  return data;
}

export async function fetchTransitions(): Promise<CsvTable> {
  const { data } = await bff.get("/capital-behavior/transitions");
  return data;
}

export async function fetchCandidates(): Promise<CandidatesPayload> {
  const { data } = await bff.get("/capital-behavior/candidates");
  return data;
}
