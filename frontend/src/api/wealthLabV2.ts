/**
 * 财富实验室 v2 API 服务层（唯一 HTTP 出口，组件内禁止直接发请求）。
 *
 * 契约：docs/contracts/wealth-lab-api-v2.yaml（/v1/wealth-lab/v2/* 扩展，v1 冻结不动）。
 * 复用 v1 降级策略：网络不可达 / 路由未部署（404）→ 契约 Mock（source="offline-mock"）；
 * 其余错误（422 参数非法 / 5xx）原样抛出。
 */

import axios from "axios";
import { errorMessage } from "./client";
import type { WealthEnvelope } from "../types/wealthLab";
import type {
  AiResearchPayload,
  AiResearchData,
  AiRiskReviewData,
  AiRiskReviewPayload,
  CompareData,
  ComparePayload,
  DecisionPacketData,
  DecisionPacketPayload,
  FactorsData,
  OptimizationData,
  OptimizationPayload,
  RiskCheckData,
  RiskCheckPayload,
  RiskReportData,
  StrategiesData,
  StrategyBacktestData,
  StrategyBacktestPayload,
  UniverseData,
} from "../types/wealthLabV2";
import {
  aiResearchMock,
  aiRiskReviewMock,
  decisionPacketMock,
  factorsMock,
  optimizationRunMock,
  riskCheckMock,
  riskReportMock,
  strategiesCompareMock,
  strategiesMock,
  strategyBacktestMock,
  universeMock,
} from "../mocks/wealthLabV2Fixtures";
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

async function withFallback<T>(live: () => Promise<WealthEnvelope<T>>, mock: () => WealthEnvelope<T>, label: string): Promise<WealthResult<T>> {
  try {
    return { envelope: await live(), source: "live" };
  } catch (err) {
    if (isBackendMissing(err)) return { envelope: mock(), source: "offline-mock" };
    throw toBusinessError(err, label);
  }
}

const get = <T>(url: string) => http.get<WealthEnvelope<T>>(url).then((r) => r.data);
const post = <T>(url: string, body: unknown) => http.post<WealthEnvelope<T>>(url, body).then((r) => r.data);

/* ---------------- 研究池 / 多因子 ---------------- */

export function fetchV2Universe(): Promise<WealthResult<UniverseData>> {
  return withFallback(() => get<UniverseData>("/v1/wealth-lab/v2/universe"), () => universeMock, "研究池");
}

export function fetchV2Factors(asof?: string): Promise<WealthResult<FactorsData>> {
  return withFallback(
    () => get<FactorsData>(`/v1/wealth-lab/v2/factors${asof ? `?asof=${asof}` : ""}`),
    () => factorsMock,
    "因子评分",
  );
}

/* ---------------- 策略工厂 ---------------- */

export function fetchV2Strategies(): Promise<WealthResult<StrategiesData>> {
  return withFallback(() => get<StrategiesData>("/v1/wealth-lab/v2/strategies"), () => strategiesMock, "策略列表");
}

export function runV2StrategyBacktest(strategyId: string, payload: StrategyBacktestPayload): Promise<WealthResult<StrategyBacktestData>> {
  return withFallback(
    () => post<StrategyBacktestData>(`/v1/wealth-lab/v2/strategies/${strategyId}/backtest`, payload),
    () => strategyBacktestMock,
    "策略回测",
  );
}

/* ---------------- 多策略对比 / 组合优化 ---------------- */

export function runV2Compare(payload: ComparePayload): Promise<WealthResult<CompareData>> {
  return withFallback(
    () => post<CompareData>("/v1/wealth-lab/v2/strategies/compare", payload),
    () => strategiesCompareMock,
    "策略比较",
  );
}

export function runV2Optimization(payload: OptimizationPayload): Promise<WealthResult<OptimizationData>> {
  return withFallback(
    () => post<OptimizationData>("/v1/wealth-lab/v2/optimization/run", payload),
    () => optimizationRunMock,
    "组合优化",
  );
}

/* ---------------- 风险核查 / AI ---------------- */

export function runV2RiskCheck(payload: RiskCheckPayload): Promise<WealthResult<RiskCheckData>> {
  return withFallback(
    () => post<RiskCheckData>("/v1/wealth-lab/v2/risk/check", payload),
    () => riskCheckMock,
    "风险核查",
  );
}

export function runV2AiResearch(payload: AiResearchPayload): Promise<WealthResult<AiResearchData>> {
  return withFallback(
    () => post<AiResearchData>("/v1/wealth-lab/v2/ai/research", payload),
    () => aiResearchMock,
    "AI 研究",
  );
}

export function runV2AiRiskReview(payload: AiRiskReviewPayload): Promise<WealthResult<AiRiskReviewData>> {
  return withFallback(
    () => post<AiRiskReviewData>("/v1/wealth-lab/v2/ai/risk-review", payload),
    () => aiRiskReviewMock,
    "AI 风险复核",
  );
}

/* ---------------- 决策包 / 风险报告 ---------------- */

export function createV2DecisionPacket(payload: DecisionPacketPayload): Promise<WealthResult<DecisionPacketData>> {
  return withFallback(
    () => post<DecisionPacketData>("/v1/wealth-lab/v2/decision-packets", payload),
    () => decisionPacketMock,
    "决策包生成",
  );
}

export function fetchV2DecisionPacket(packetId: string): Promise<WealthResult<DecisionPacketData>> {
  return withFallback(
    () => get<DecisionPacketData>(`/v1/wealth-lab/v2/decision-packets/${packetId}`),
    () => decisionPacketMock,
    "决策包查询",
  );
}

export function fetchV2RiskReport(backtestId: string): Promise<WealthResult<RiskReportData>> {
  return withFallback(
    () => get<RiskReportData>(`/v1/wealth-lab/v2/reports/${backtestId}`),
    () => riskReportMock,
    "风险报告",
  );
}
