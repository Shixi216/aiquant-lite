import { bff } from "./client";

export interface CsvTable {
  columns: string[];
  rows: Record<string, unknown>[];
}

export interface WalkForwardRow {
  candidate_id: string;
  fold: number;
  train_window_days: number;
  validation_start: string;
  validation_end: string;
  sharpe: number | null;
  sortino: number | null;
  annualized_return: number | null;
  max_drawdown: number | null;
  win_rate: number | null;
  calmar: number | null;
}

export interface YearlyRow {
  candidate_id: string;
  period: string;
  sharpe: number | null;
  sortino: number | null;
  annualized_return: number | null;
  max_drawdown: number | null;
  calmar: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  tail_loss: number | null;
}

export interface CostRow {
  candidate_id: string;
  cost_bp: number;
  sharpe: number | null;
  sortino: number | null;
  max_drawdown: number | null;
  annualized_return: number | null;
}

export interface SentimentRun {
  run: string;
  alpha_conclusion: string | null;
  date_from: string | null;
  date_to: string | null;
  date_count: number | null;
  evaluation_row_count: number | null;
  algorithm_version: string | null;
}

export interface DecayCurvePoint {
  horizon: number;
  score_method: string;
  mean_ic: number;
  ic_ir: number;
  ic_std: number;
  positive_ic_ratio: number;
}

export async function fetchStrategyLab(name: string): Promise<CsvTable> {
  const { data } = await bff.get(`/research/strategy-lab/${name}`);
  return data;
}

export async function fetchSentimentRuns(): Promise<{ runs: SentimentRun[] }> {
  const { data } = await bff.get("/research/sentiment-eval/runs");
  return data;
}

export async function fetchDecayCurve(run: string): Promise<{ curves: DecayCurvePoint[] }> {
  const { data } = await bff.get(`/research/sentiment-eval/${run}/decay-curve`);
  return data;
}

export async function fetchIcSummary(run: string): Promise<CsvTable> {
  const { data } = await bff.get(`/research/sentiment-eval/${run}/ic-summary`);
  return data;
}

export async function fetchStratifiedReturns(run: string): Promise<CsvTable> {
  const { data } = await bff.get(`/research/sentiment-eval/${run}/stratified-returns`);
  return data;
}
