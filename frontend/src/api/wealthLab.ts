/**
 * 财富管理实验室 API 服务层（唯一 HTTP 出口，组件内禁止直接发请求）。
 *
 * 契约：docs/contracts/wealth-lab-api-v1.yaml（v1 已冻结 2026-10-24）
 * 服务器：本地 Router http://127.0.0.1:8765（vite 代理 /wealth-lab-api → 8765）。
 *
 * 降级策略：网络不可达或 /v1/wealth-lab/* 路由未部署（404）时，
 * 自动回落到 docs/contracts/mocks 的契约样例（source="offline-mock"），
 * 页面必须显著标注“契约 Mock 演示数据”；其余错误（422 参数非法 / 5xx）原样抛出。
 */

import axios from "axios";
import { errorMessage } from "./client";
import type {
  AssetsData,
  BacktestData,
  BacktestParams,
  MetaData,
  RiskData,
  StressTestData,
  StressTestPayload,
  WealthEnvelope,
} from "../types/wealthLab";
import {
  assetsMock,
  backtestRealMock,
  metaMock,
  riskMock,
  stressHistoricalMock,
  stressSyntheticMock,
} from "../mocks/wealthLabFixtures";

/** 与 vite.config.ts 中 /wealth-lab-api 代理一一对应；后端换端口/前缀时同步修改两处。 */
export const WEALTH_LAB_API_BASE = "/wealth-lab-api";

export type DataSource = "live" | "offline-mock";

export interface WealthResult<T> {
  envelope: WealthEnvelope<T>;
  source: DataSource;
}

const http = axios.create({ baseURL: WEALTH_LAB_API_BASE, timeout: 45000 });

function isBackendMissing(err: unknown): boolean {
  if (!axios.isAxiosError(err)) return false;
  if (!err.response) return true; // 网络不可达 / 超时
  return err.response.status === 404; // 路由尚未部署
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

async function withFallback<T>(live: () => Promise<WealthEnvelope<T>>, mock: () => WealthEnvelope<T>, label: string): Promise<WealthResult<T>> {
  try {
    return { envelope: await live(), source: "live" };
  } catch (err) {
    if (isBackendMissing(err)) return { envelope: mock(), source: "offline-mock" };
    throw toBusinessError(err, label);
  }
}

/* ---------------- GET /v1/wealth-lab/meta ---------------- */

export function fetchWealthMeta(): Promise<WealthResult<MetaData>> {
  return withFallback(
    () => http.get<WealthEnvelope<MetaData>>("/v1/wealth-lab/meta").then((r) => r.data),
    () => metaMock,
    "实验室元信息",
  );
}

/* ---------------- GET /v1/wealth-lab/assets ---------------- */

export function fetchWealthAssets(): Promise<WealthResult<AssetsData>> {
  return withFallback(
    () => http.get<WealthEnvelope<AssetsData>>("/v1/wealth-lab/assets").then((r) => r.data),
    () => assetsMock,
    "资产列表",
  );
}

/* ---------------- POST /v1/wealth-lab/backtests/run ---------------- */

export function runWealthBacktest(payload: BacktestParams): Promise<WealthResult<BacktestData>> {
  return withFallback(
    () => http.post<WealthEnvelope<BacktestData>>("/v1/wealth-lab/backtests/run", payload).then((r) => r.data),
    () => backtestRealMock,
    "组合回测",
  );
}

/* ---------------- GET /v1/wealth-lab/backtests/{backtest_id}/risk ---------------- */

export function fetchWealthRisk(backtestId: string): Promise<WealthResult<RiskData>> {
  return withFallback(
    () => http.get<WealthEnvelope<RiskData>>(`/v1/wealth-lab/backtests/${backtestId}/risk`).then((r) => r.data),
    () => riskMock,
    "风险指标",
  );
}

/* ---------------- POST /v1/wealth-lab/stress-tests ---------------- */

export function runWealthStressTest(payload: StressTestPayload): Promise<WealthResult<StressTestData>> {
  const mock = payload.scenario.kind === "historical" ? stressHistoricalMock : stressSyntheticMock;
  return withFallback(
    () => http.post<WealthEnvelope<StressTestData>>("/v1/wealth-lab/stress-tests", payload).then((r) => r.data),
    () => mock,
    "压力测试",
  );
}
