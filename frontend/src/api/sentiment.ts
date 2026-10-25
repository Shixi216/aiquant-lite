/**
 * 多资产市场情绪 API 服务层（唯一 HTTP 出口）。
 * 契约：trading/wealth_lab/sentiment/CONTRACT.md（wealth-sentiment/v1.1）。
 * 读端点直连情绪模块；三个写操作走受控 Jobs API（8765，白名单 3 操作），
 * 返回 job_id 后由调用方复用 v3 jobs 轮询（api/taskCenter.ts 的 fetchJob）。
 * 无假成功：任务真实执行、失败如实返回。
 */

import axios from "axios";

import { errorMessage } from "./client";
import type { WealthEnvelope } from "../types/wealthLab";
import type {
  AssetNewsItem,
  AssetScoreData,
  MarketSentimentData,
  NewsEventsData,
  ScoreEvidenceDetail,
  SentimentJobSubmitData,
  SourceInfo,
} from "../types/sentiment";

const http = axios.create({ baseURL: "/wealth-lab-api", timeout: 60_000 });

function unwrap<T>(payload: WealthEnvelope<T>): T {
  return payload.data;
}

function toBusinessError(err: unknown, label: string): Error {
  if (axios.isAxiosError(err)) {
    const envelope = err.response?.data as
      | { detail?: unknown; sanitized_error?: string | null; error_code?: string | null }
      | undefined;
    const detail = envelope?.sanitized_error || envelope?.error_code
      || (typeof envelope?.detail === "string" ? envelope.detail : undefined);
    if (detail) return new Error(`${label}：${detail}`);
    const httpStatus = err.response?.status;
    if (httpStatus) return new Error(`${label}：HTTP ${httpStatus}（响应非契约格式：${String(err.response?.data ?? "").slice(0, 80)}）`);
  }
  // 非 AxiosError：保留原始错误类型（如 JSON 解析失败），便于定位
  const raw = err as { name?: string; message?: string };
  return new Error(`${label}：${raw?.name ?? "Error"}：${String(raw?.message ?? err).slice(0, 140)}`);
}

/* ---------------- 读端点 ---------------- */

export async function fetchMarketSentiment(): Promise<MarketSentimentData> {
  try {
    const response = await http.get<WealthEnvelope<MarketSentimentData>>("/v1/wealth-lab/sentiment/market");
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "读取市场情绪失败");
  }
}

export async function fetchMarketHistory(days = 30): Promise<{ data_mode: string; history: { asof: string; sentiment_score: number; confidence: number }[] }> {
  try {
    const response = await http.get<WealthEnvelope<{ data_mode: string; history: { asof: string; sentiment_score: number; confidence: number }[] }>>(
      "/v1/wealth-lab/sentiment/market/history", { params: { days } });
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "读取市场情绪历史失败");
  }
}

export async function fetchAssetScore(symbol: string): Promise<AssetScoreData> {
  try {
    const response = await http.get<WealthEnvelope<AssetScoreData>>(
      `/v1/wealth-lab/sentiment/assets/${encodeURIComponent(symbol)}/score`);
    return unwrap(response.data);
  } catch (err) {
    // 调试：保留原始错误（code/message），由视图层统一呈现
    if (axios.isAxiosError(err)) {
      const wrapped = new Error(`[${err.code ?? "HTTP_" + String(err.response?.status ?? 0)}] ${err.message}`);
      throw wrapped;
    }
    throw err;
  }
}

export async function fetchAssetNews(symbol: string, limit = 20): Promise<{ data_mode: string; items: AssetNewsItem[] }> {
  try {
    const response = await http.get<WealthEnvelope<{ data_mode: string; items: AssetNewsItem[] }>>(
      `/v1/wealth-lab/sentiment/assets/${encodeURIComponent(symbol)}/news`, { params: { limit } });
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, `读取 ${symbol} 资讯失败`);
  }
}

export interface NewsEventQuery {
  category?: string;
  symbol?: string;
  analysis_status?: string;
  since?: string;
  limit?: number;
}

export async function fetchNewsEvents(query: NewsEventQuery = {}): Promise<NewsEventsData> {
  try {
    const response = await http.get<WealthEnvelope<NewsEventsData>>("/v1/wealth-lab/sentiment/news/events", {
      params: Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== "")),
    });
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "读取新闻事件失败");
  }
}

export async function fetchSources(): Promise<{ data_mode: string; sources: SourceInfo[]; [key: string]: unknown }> {
  try {
    const response = await http.get<WealthEnvelope<{ data_mode: string; sources: SourceInfo[] }>>("/v1/wealth-lab/sentiment/sources");
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "读取数据源状态失败");
  }
}

export async function fetchScoreEvidence(scoreId: string): Promise<ScoreEvidenceDetail> {
  try {
    const response = await http.get<WealthEnvelope<ScoreEvidenceDetail>>(
      `/v1/wealth-lab/sentiment/scores/${encodeURIComponent(scoreId)}/evidence`);
    return unwrap(response.data);
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) {
      throw new Error("评分快照不存在");
    }
    throw toBusinessError(err, "读取评分证据失败");
  }
}

/* ---------------- 写任务（Jobs API，白名单 3 操作） ---------------- */

export async function submitNewsRefresh(body: { sources?: string[]; fund_symbols?: string[] } = {}): Promise<SentimentJobSubmitData> {
  try {
    const response = await http.post<WealthEnvelope<SentimentJobSubmitData>>("/v1/wealth-lab/sentiment/refresh", body);
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "提交资讯更新任务失败");
  }
}

export async function submitSentimentAnalysis(limit = 8): Promise<SentimentJobSubmitData> {
  try {
    const response = await http.post<WealthEnvelope<SentimentJobSubmitData>>(
      "/v1/wealth-lab/sentiment/analyze", { limit, confirm: true });
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "提交情绪分析任务失败");
  }
}

export async function submitSentimentRebuild(lookbackHours = 168): Promise<SentimentJobSubmitData> {
  try {
    const response = await http.post<WealthEnvelope<SentimentJobSubmitData>>(
      "/v1/wealth-lab/sentiment/rebuild-factor", { lookback_hours: lookbackHours });
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "提交因子重建任务失败");
  }
}
