import { bff } from "./client";

export interface PoolEntry {
  symbol: string;
  name: string;
  group: string;
  price: number;
}

export interface TimepointInfo {
  key: string;
  zh: string;
  generated_at: string;
  result_count: number;
}

export interface DayInventory {
  date: string;
  frozen_date: string;
  frozen_at: string | null;
  pool: PoolEntry[];
  timepoints: TimepointInfo[];
}

export interface FiveDim {
  技术: number | null;
  基本: number | null;
  情绪: number | null;
  政策: number | null;
  资金: number | null;
}

export interface SnapshotRow {
  symbol: string;
  name: string;
  status: "OK" | "ERROR" | string;
  error?: string;
  决策时间?: string;
  当前动作?: string;
  总评分?: number;
  五维结论?: FiveDim;
  盘中强度?: string;
  波段质量?: string;
  仓位建议?: string;
  参考价格?: number;
  后续表现?: { 信号价?: number; 当前价?: number; 涨跌幅?: number; 表现?: string };
  止损止盈?: unknown;
  VETO状态?: string;
  相比上次变化?: string;
  变化原因?: string;
  观察组?: string;
}

export interface Snapshot {
  timepoint: string;
  timepoint_zh: string;
  generated_at: string;
  results: SnapshotRow[];
}

export interface SeriesPoint {
  timepoint: string;
  timepoint_zh: string;
  generated_at: string;
  status: string;
  action: string | null;
  score: number | null;
  price: number | null;
  current_price: number | null;
  pct: number | null;
  position_advice: string | null;
  veto: string | null;
  group: string | null;
}

export interface SymbolSeries {
  date: string;
  symbol: string;
  name: string | null;
  series: SeriesPoint[];
}

export async function fetchDates(): Promise<{ dates: string[]; latest: string | null }> {
  const { data } = await bff.get("/sim-daily/dates");
  return data;
}

export async function fetchDayInventory(date: string): Promise<DayInventory> {
  const { data } = await bff.get(`/sim-daily/dates/${date}`);
  return data;
}

export async function fetchSnapshot(date: string, timepoint: string): Promise<Snapshot> {
  const { data } = await bff.get(`/sim-daily/dates/${date}/timepoints/${timepoint}`);
  return data;
}

export async function fetchSymbolSeries(date: string, symbol: string): Promise<SymbolSeries> {
  const { data } = await bff.get(`/sim-daily/dates/${date}/symbols/${symbol}`);
  return data;
}
