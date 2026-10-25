/**
 * v3.3 AI 智能研究 API 服务层（唯一 HTTP 出口）。
 * 契约：trading/wealth_lab/routes_research.py（/v1/wealth-lab/v3/research/*）
 *       + routes_v2.py（决策包 /v1/wealth-lab/v2/decision-packets/{id}）。
 * 原则：只调用实际接口、原样透出后端状态与错误；不做任何进度模拟；
 *       付费调用必须由用户显式确认（confirm_paid_calls / 实验 flag 由后端强制）。
 */

import axios from "axios";

import { errorMessage } from "./client";
import type { WealthEnvelope } from "../types/wealthLab";
import type {
  ResearchRolesData,
  ResearchRunRecord,
  ResearchRunResult,
  WealthDecisionPacket,
} from "../types/aiResearch";

/** 研究为长同步链路（后端总超时 720s），HTTP 超时放宽到 12.5 分钟。 */
const http = axios.create({ baseURL: "/wealth-lab-api", timeout: 780_000 });

function unwrap<T>(payload: WealthEnvelope<T>): T {
  return payload.data;
}

function toBusinessError(err: unknown, label: string): Error {
  if (axios.isAxiosError(err)) {
    const envelope = err.response?.data as
      | { status?: string; error_code?: string | null; sanitized_error?: string | null; data?: Record<string, unknown>; warnings?: string[] }
      | undefined;
    const detail = envelope?.sanitized_error || envelope?.error_code
      || (typeof (envelope as { detail?: unknown })?.detail === "string" ? (envelope as { detail: string }).detail : undefined);
    if (detail) {
      const wrapped = new Error(`${label}：${detail}`) as Error & { payload?: Record<string, unknown>; warnings?: string[]; httpStatus?: number };
      wrapped.payload = (envelope?.data && typeof envelope.data === "object" ? envelope.data : undefined);
      wrapped.warnings = envelope?.warnings;
      wrapped.httpStatus = err.response?.status;
      return wrapped;
    }
  }
  return new Error(`${label}：${errorMessage(err)}`);
}

/* ---------------- GET /research/roles ---------------- */

export async function fetchResearchRoles(): Promise<ResearchRolesData> {
  try {
    const response = await http.get<WealthEnvelope<ResearchRolesData>>("/v1/wealth-lab/v3/research/roles");
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "读取模型角色失败");
  }
}

/* ---------------- POST /research/runs/stable ---------------- */

export interface StableRunPayload {
  backtest_id: string;
  confirm_paid_calls: boolean;
  allow_reuse: boolean;
}

export async function runStableResearch(payload: StableRunPayload): Promise<ResearchRunResult> {
  try {
    const response = await http.post<WealthEnvelope<ResearchRunResult>>("/v1/wealth-lab/v3/research/runs/stable", payload);
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "稳定模式研究失败");
  }
}

/* ---------------- POST /research/runs/experimental ---------------- */

export async function startExperimentalResearch(goal: string): Promise<ResearchRunResult> {
  try {
    const response = await http.post<WealthEnvelope<ResearchRunResult>>(
      "/v1/wealth-lab/v3/research/runs/experimental",
      { goal },
    );
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "实验模式规划失败");
  }
}

/* ---------------- POST /research/runs/{id}/approve ---------------- */

export async function approveResearchRun(researchRunId: string): Promise<ResearchRunResult> {
  try {
    const response = await http.post<WealthEnvelope<ResearchRunResult>>(
      `/v1/wealth-lab/v3/research/runs/${encodeURIComponent(researchRunId)}/approve`,
    );
    return unwrap(response.data);
  } catch (err) {
    throw toBusinessError(err, "研究执行失败");
  }
}

/* ---------------- GET /research/runs/{id} ---------------- */

export async function fetchResearchRun(researchRunId: string): Promise<ResearchRunRecord> {
  try {
    const response = await http.get<WealthEnvelope<ResearchRunRecord>>(
      `/v1/wealth-lab/v3/research/runs/${encodeURIComponent(researchRunId)}`,
    );
    return unwrap(response.data);
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) {
      throw new Error("研究运行不存在：请核对 run_id（形如 wlrun_xxxxxxxxxxxx）");
    }
    throw toBusinessError(err, "读取研究状态失败");
  }
}

/* ---------------- GET /v2/decision-packets/{id} ---------------- */

export async function fetchDecisionPacket(packetId: string): Promise<WealthDecisionPacket> {
  try {
    const response = await http.get<WealthEnvelope<WealthDecisionPacket>>(
      `/v1/wealth-lab/v2/decision-packets/${encodeURIComponent(packetId)}`,
    );
    return unwrap(response.data);
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) {
      throw new Error("决策包不存在");
    }
    throw toBusinessError(err, "读取决策包失败");
  }
}
