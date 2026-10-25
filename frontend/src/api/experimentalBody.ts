/**
 * 实验模式请求体构造（纯函数，零依赖，可独立单测）。
 * 审计复审 P1-1：confirm_paid_calls 必须是真实布尔 true 才发送；
 * 字符串 "true"/"false"、undefined 一律按未确认处理（false），防止伪装。
 */

export interface ExperimentalRunBody {
  goal: string;
  confirm_paid_calls: boolean;
}

export function experimentalRunBody(
  goal: string,
  confirmPaidCalls: boolean,
): ExperimentalRunBody {
  return { goal, confirm_paid_calls: confirmPaidCalls === true };
}
