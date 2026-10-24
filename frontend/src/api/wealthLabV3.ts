/**
 * 财富实验室 v3 API 服务层（唯一 HTTP 出口）。
 * 契约：docs/contracts/wealth-lab-api-v3.yaml（/v1/wealth-lab/v3/*）。
 * 后端未部署（404/网络不可达）时降级到契约 Mock 并标注 source="offline-mock"；
 * 其余错误原样抛出——不得伪造后端结果。
 */

import axios from "axios";
import { errorMessage } from "./client";
import type { WealthEnvelope } from "../types/wealthLab";
import type {
  EligibleData,
  QualityData,
  UniversesData,
  UniverseAssetsData,
  UniverseBacktestData,
  UniverseBacktestPayload,
} from "../types/wealthLabV3";
import {
  eligibleMock,
  qualityMock,
  universesMock,
  universeAssetsMock,
  universeBacktestMock,
} from "../mocks/wealthLabV3Fixtures";
import type { DataSource, WealthResult } from "./wealthLab";

const http = axios.create({ baseURL: "/wealth-lab-api", timeout: 60000 });

function isBackendMissing(err: unknown): boolean {
  if (!axios.isAxiosError(err)) return false;
  if (!err.response) return true;
  return err.response.status === 404;
}

function toBusinessError(err: unknown, label: string): Error {
  if (axios.isAxiosError(err)) {
    const payload = err.response?.data as
      | { error_code?: string | null; sanitized_error?: string | null }
      | undefined;
    const detail = payload?.sanitized_error || payload?.error_code;
    if (detail) return new Error(`${label}失败：${detail}`);
  }
  return new Error(`${label}失败：${errorMessage(err)}`);
}

/** HTTP 200 + status=FAILED（如 PIT 闸门拒绝）的富信息错误：携带信封内数据供页面展示。 */
export class V3BacktestRejectedError extends Error {
  /** FAILED 信封的 data（v3.2 含 requested_start_date / suggested_start_date / excluded_reasons）。 */
  readonly rejection: Record<string, unknown>;

  constructor(label: string, message: string, rejection: Record<string, unknown>) {
    super(`${label}失败：${message}`);
    this.name = "V3BacktestRejectedError";
    this.rejection = rejection;
  }
}

function toBusinessErrorRich(err: unknown, label: string): Error {
  if (axios.isAxiosError(err)) {
    const envelope = err.response?.data as
      | { status?: string; error_code?: string | null; sanitized_error?: string | null; data?: Record<string, unknown> }
      | undefined;
    const detail = envelope?.sanitized_error || envelope?.error_code;
    if (detail) {
      if (envelope?.status === "FAILED" && envelope.data && typeof envelope.data === "object") {
        return new V3BacktestRejectedError(label, detail, envelope.data);
      }
      return new Error(`${label}失败：${detail}`);
    }
  }
  return toBusinessError(err, label);
}

async function withFallback<T>(live: () => Promise<WealthEnvelope<T>>, mock: () => WealthEnvelope<T>, label: string, onBusinessError?: (err: unknown) => Error): Promise<WealthResult<T>> {
  try {
    return { envelope: await live(), source: "live" };
  } catch (err) {
    if (isBackendMissing(err)) return { envelope: mock(), source: "offline-mock" };
    throw onBusinessError ? onBusinessError(err) : toBusinessError(err, label);
  }
}

const get = <T>(url: string) => http.get<WealthEnvelope<T>>(url).then((r) => r.data);

/* ---------------- GET /v1/wealth-lab/v3/universes ---------------- */

export function fetchV3Universes(): Promise<WealthResult<UniversesData>> {
  return withFallback(() => get<UniversesData>("/v1/wealth-lab/v3/universes"), () => universesMock, "资产池列表");
}

/* ---------------- GET /v1/wealth-lab/v3/universes/{universe_id}/assets ---------------- */

export function fetchV3UniverseAssets(universeId: string): Promise<WealthResult<UniverseAssetsData>> {
  return withFallback(
    () => get<UniverseAssetsData>(`/v1/wealth-lab/v3/universes/${universeId}/assets`),
    () => universeAssetsMock,
    "池内资产",
  );
}

/* ---------------- GET /v1/wealth-lab/v3/quality?category=&status=&q=&page=&limit= ---------------- */

/** v3.1：分页 + 类别过滤；v3.2 增量：status（质量状态）与 q（代码/名称关键字）服务端参数。 */
export function fetchV3Quality(
  category?: string,
  page?: number,
  limit?: number,
  status?: string,
  q?: string,
): Promise<WealthResult<QualityData>> {
  const params: Record<string, string | number> = {};
  if (category) params.category = category;
  if (status && status !== "all") params.status = status;
  if (q && q.trim()) params.q = q.trim();
  if (page) params.page = page;
  if (limit) params.limit = limit;
  return withFallback(
    () => http.get<WealthEnvelope<QualityData>>("/v1/wealth-lab/v3/quality", { params }).then((r) => r.data),
    () => qualityMock,
    "数据质量审计",
  );
}

/* ---------------- GET /v1/wealth-lab/v3/eligible?universe_id=&asof= ---------------- */

export function fetchV3Eligible(universeId: string, asof: string): Promise<WealthResult<EligibleData>> {
  return withFallback(
    () => get<EligibleData>(`/v1/wealth-lab/v3/eligible?universe_id=${encodeURIComponent(universeId)}&asof=${asof}`),
    () => eligibleMock,
    "历史可投资判定",
  );
}

/* ---------------- POST /v1/wealth-lab/v3/strategies/{strategy_id}/backtest ---------------- */

export function runV3UniverseBacktest(strategyId: string, payload: UniverseBacktestPayload): Promise<WealthResult<UniverseBacktestData>> {
  return withFallback(
    () => http.post<WealthEnvelope<UniverseBacktestData>>(`/v1/wealth-lab/v3/strategies/${strategyId}/backtest`, payload).then((r) => r.data),
    () => universeBacktestMock,
    "资产池回测",
    toBusinessErrorRich,
  );
}
