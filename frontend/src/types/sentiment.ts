/**
 * Wealth Sentiment v1.1 类型定义。
 * 1:1 镜像 trading/wealth_lab/sentiment/CONTRACT.md（wealth-sentiment/v1）。
 * CURRENT_RESEARCH_ONLY：仅研究界面可读，不接入 S0-S8 正式策略。
 */

import type { WealthEnvelope } from "./wealthLab";
import type { JobPublic } from "../api/taskCenter";

export type { WealthEnvelope, JobPublic };

/** 证据基础：直接证据 / 仅类别推导 / 证据不足。 */
export type EvidenceBasis = "DIRECT_EVIDENCE" | "CLASS_LEVEL_EVIDENCE" | "INSUFFICIENT_DATA";

/** 事件与资产的关联分级（v1.1）：只有 EXACT 构成直接证据。 */
export type AssetLinkLevel = "EXACT" | "SAME_INDEX" | "CLASS_ONLY" | "MODEL_INFERRED";

export interface SentimentScore {
  score_id: string;
  level: "MARKET" | "ASSET" | string;
  symbol: string | null;
  asof: string;
  /** -100 ~ +100，研究指标，非收益预测 */
  sentiment_score: number;
  /** 0~1；CLASS_LEVEL_EVIDENCE 封顶 0.6 */
  confidence: number;
  coverage: number;
  coverage_ratio: number;
  observation_count: number;
  source_count: number;
  sentiment_version: string;
  evidence_basis: EvidenceBasis;
  evidence_count: number;
  direct_evidence_count: number;
  class_evidence_count: number;
  last_evidence_at: string | null;
  evidence_ids: string[];
  component_breakdown?: {
    by_direction?: Record<string, number>;
    by_link_level?: Record<string, number>;
    top_contributors?: {
      event_id: string;
      title: string;
      direction: string;
      signed: number;
      link_level: string | null;
      decay: number;
      confidence: number;
    }[];
    weights?: Record<string, unknown>;
    lookback_hours?: number;
    events_in_window?: number;
  };
}

export interface MarketSentimentData {
  data_mode: string;
  score: SentimentScore | null;
  evidence_basis?: EvidenceBasis;
  message?: string;
}

export interface AssetScoreData {
  data_mode: string;
  symbol: string;
  score: SentimentScore | null;
  evidence_basis?: EvidenceBasis;
  message?: string;
}

export interface AssetLink {
  level: AssetLinkLevel;
  rule: string;
}

export interface NewsEvent {
  event_id: string;
  title: string;
  published_at: string;
  published_at_precision?: string;
  raw_category?: string;
  asset_scope: string[];
  asset_links: Record<string, AssetLink> | null;
  news_count: number;
  source_count: number;
  analysis_status: "ANALYZED" | "INSUFFICIENT_DATA" | "MERGED" | "PENDING" | string;
  merged_into: string | null;
  first_seen_at: string;
  sentiment_direction: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | "MIXED" | null;
  analysis_id: string | null;
}

export interface NewsEventsData {
  data_mode: string;
  events: NewsEvent[];
  [key: string]: unknown;
}

export interface AssetNewsItem {
  news_id?: string;
  title: string;
  source?: string;
  published_at?: string;
  url?: string | null;
  asset_links?: Record<string, AssetLink> | null;
  [key: string]: unknown;
}

export interface SourceInfo {
  source_id?: string;
  name?: string;
  status?: string;
  kind?: string;
  [key: string]: unknown;
}

export interface ScoreEvidenceDetail {
  score: SentimentScore;
  analyses: {
    analysis_id: string;
    event_id?: string;
    title?: string;
    sentiment_direction?: string | null;
    affected_symbols?: string[];
    affected_asset_classes?: string[];
    evidence_spans?: unknown;
    validation_flags?: string[];
    [key: string]: unknown;
  }[];
  [key: string]: unknown;
}

/** 情绪任务提交响应（data 内直接是 job 公开字段）。 */
export type SentimentJobSubmitData = JobPublic & { duplicated: boolean; message: string };
