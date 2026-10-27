import type { MarketBreadthData } from "../types/v2";

export const DASHBOARD_MIN_COVERAGE = 0.99;
export const DASHBOARD_MIN_FACTOR_CONFIDENCE = 0.5;

export interface DashboardResearchFactor {
  score: number;
  confidence: number;
  data_cutoff: string;
  formal_status?: string;
}

export function acceptedMarketBreadth(data: MarketBreadthData | null | undefined): boolean {
  if (!data) return false;
  return (
    data.completeness_status === "COMPLETE" &&
    Number.isFinite(data.coverage_ratio) &&
    data.coverage_ratio >= DASHBOARD_MIN_COVERAGE &&
    data.valid_symbol_count > 0
  );
}

export function marketCoverageLabel(data: MarketBreadthData): string {
  const coverage = Math.max(0, Math.min(1, data.coverage_ratio));
  return `${(coverage * 100).toFixed(1)}% · ${data.valid_symbol_count}只`;
}

export function displayableResearchFactor(value: unknown): DashboardResearchFactor | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const score = Number(raw.score);
  const confidence = Number(raw.confidence);
  const dataCutoff = typeof raw.data_cutoff === "string" ? raw.data_cutoff : "";
  if (
    !Number.isFinite(score) ||
    !Number.isFinite(confidence) ||
    confidence < DASHBOARD_MIN_FACTOR_CONFIDENCE ||
    !dataCutoff
  ) return null;
  return {
    score,
    confidence,
    data_cutoff: dataCutoff,
    formal_status: typeof raw.formal_status === "string" ? raw.formal_status : undefined,
  };
}
