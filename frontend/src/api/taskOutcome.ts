/**
 * 同步任务终态判定（纯函数，无 IO 依赖，可独立单测）。
 *
 * 审计 P1-4：HTTP 200 不等于业务成功——信封 status=FAILED 或
 * data_mode=UNAVAILABLE 必须记为失败；DEGRADED 如实呈现为业务降级，
 * 保留 sanitized_error，不得落为成功或 Mock。
 */

export type SyncOutcomeStatus = "SUCCEEDED" | "DEGRADED" | "FAILED";

export interface EnvelopeLike {
  status?: unknown;
  data?: unknown;
  sanitized_error?: unknown;
  error_code?: unknown;
  warnings?: unknown;
}

function envelopeData(payload: EnvelopeLike | unknown): Record<string, unknown> {
  const outer = payload as { data?: unknown };
  if (outer && typeof outer === "object" && "data" in outer && outer.data && typeof outer.data === "object" && !Array.isArray(outer.data)) {
    return outer.data as Record<string, unknown>;
  }
  return (payload ?? {}) as Record<string, unknown>;
}

function firstText(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

export interface SyncOutcome {
  status: SyncOutcomeStatus;
  /** 业务失败/降级时给用户看的中文说明（来自 sanitized_error/error_code/reason） */
  businessMessage: string | null;
}

/**
 * 按后端信封判定同步任务终态。
 * - 信封 status=FAILED 或内层 data_mode=UNAVAILABLE → FAILED；
 * - 信封 status=DEGRADED → DEGRADED（如实展示，不冒充成功）；
 * - 无信封 status 字段的裸对象响应（如部分 data_hub 接口）→ SUCCEEDED；
 * - reason/sanitized_error 始终透出，供记录与展示。
 */
export function resolveSyncOutcome(payload: unknown): SyncOutcome {
  const outer = (payload ?? {}) as EnvelopeLike;
  const hasEnvelope = outer && typeof outer === "object" && "status" in outer && typeof outer.status === "string";
  const envelopeStatus = hasEnvelope ? String(outer.status).toUpperCase() : null;
  const data = envelopeData(payload);
  const dataMode = typeof data.data_mode === "string" ? data.data_mode.toUpperCase() : null;
  const businessMessage = firstText(
    outer.sanitized_error,
    outer.error_code,
    typeof data.reason === "string" ? data.reason : null,
    typeof data.message === "string" ? data.message : null,
    typeof (data as { sanitized_error?: unknown }).sanitized_error === "string"
      ? (data as { sanitized_error: string }).sanitized_error
      : null,
  );

  if (envelopeStatus === "FAILED" || dataMode === "UNAVAILABLE") {
    return {
      status: "FAILED",
      businessMessage: businessMessage ?? "后端返回业务失败（HTTP 200），请查看响应原文",
    };
  }
  if (envelopeStatus === "DEGRADED") {
    return {
      status: "DEGRADED",
      businessMessage: businessMessage ?? "后端返回业务降级结果",
    };
  }
  return { status: "SUCCEEDED", businessMessage: null };
}
