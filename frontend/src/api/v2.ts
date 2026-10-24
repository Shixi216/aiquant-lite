import { api, bff, errorMessage } from "./client";
import type {
  ChartResponse,
  DashboardResponse,
  OpportunitiesResponse,
  StockIntelResponse,
  StockSearchResponse,
} from "../types/v2";

export async function fetchDashboard(): Promise<DashboardResponse> {
  const response = await bff.get<DashboardResponse>("/v2/dashboard");
  return response.data;
}

export async function fetchOpportunities(params: {
  limit?: number;
  offset?: number;
  min_score?: number | undefined;
  sort?: string;
}): Promise<OpportunitiesResponse> {
  const response = await bff.get<OpportunitiesResponse>("/v2/opportunities", { params });
  return response.data;
}

export async function fetchStockIntel(symbol: string): Promise<StockIntelResponse> {
  const response = await bff.get<StockIntelResponse>(`/v2/stocks/${symbol}`);
  return response.data;
}

export async function searchStocks(query: string, limit = 8): Promise<StockSearchResponse> {
  const response = await api.get<StockSearchResponse>("/v1/equity-context/search", {
    params: { query, limit },
  });
  return response.data;
}

export async function fetchChart(symbol: string, days: number): Promise<ChartResponse> {
  const response = await bff.get<ChartResponse>(`/v2/stocks/${symbol}/chart`, {
    params: { days },
  });
  return response.data;
}

export { errorMessage };

/* ---------------- 量化面板 ---------------- */

import type {
  BreadthPoint,
  DragonTigerItem,
  LatestSessionData,
  MarginPoint,
  QuantEnvelope,
  QuantOverviewData,
  RpsData,
  ScanData,
  LimitPoolsData,
} from "../types/v2";

export async function fetchQuantOverview(): Promise<QuantEnvelope<QuantOverviewData>> {
  const response = await bff.get<QuantEnvelope<QuantOverviewData>>("/v2/quant/overview");
  return response.data;
}

export async function fetchQuantBreadth(days = 60): Promise<QuantEnvelope<BreadthPoint[]>> {
  const response = await bff.get<QuantEnvelope<BreadthPoint[]>>("/v2/quant/breadth", { params: { days } });
  return response.data;
}

export async function fetchQuantMargin(days = 60): Promise<QuantEnvelope<MarginPoint[]>> {
  const response = await bff.get<QuantEnvelope<MarginPoint[]>>("/v2/quant/margin", { params: { days } });
  return response.data;
}

export async function fetchQuantDragonTiger(limit = 30): Promise<QuantEnvelope<DragonTigerItem[]>> {
  const response = await bff.get<QuantEnvelope<DragonTigerItem[]>>("/v2/quant/dragon-tiger", { params: { limit } });
  return response.data;
}

export async function fetchQuantLimitPools(limit = 30): Promise<QuantEnvelope<LimitPoolsData>> {
  const response = await bff.get<QuantEnvelope<LimitPoolsData>>("/v2/quant/limit-pools", { params: { limit } });
  return response.data;
}

export async function fetchQuantScan(type: string, limit = 50): Promise<QuantEnvelope<ScanData>> {
  const response = await bff.get<QuantEnvelope<ScanData>>("/v2/quant/scan", { params: { type, limit } });
  return response.data;
}

export async function fetchQuantRps(days = 60, minRps = 90, limit = 50): Promise<QuantEnvelope<RpsData>> {
  const response = await bff.get<QuantEnvelope<RpsData>>("/v2/quant/rps", { params: { days, min_rps: minRps, limit } });
  return response.data;
}

export async function fetchQuantLatestSession(): Promise<QuantEnvelope<LatestSessionData>> {
  const response = await bff.get<QuantEnvelope<LatestSessionData>>("/v2/quant/latest");
  return response.data;
}
