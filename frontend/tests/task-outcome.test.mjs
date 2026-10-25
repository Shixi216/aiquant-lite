/**
 * 同步任务终态判定单测（审计 P1-4 回归守卫）。
 * 复现场景来自 reports/release_review_20261025_e60c328/frontend-reproductions.json：
 * HTTP 200 + status=FAILED + data_mode=UNAVAILABLE 曾被记为 SUCCEEDED。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

const { resolveSyncOutcome } = await import("../src/api/taskOutcome.ts");

test("HTTP 200 + 信封 FAILED + data_mode=UNAVAILABLE 必须判为失败", () => {
  const outcome = resolveSyncOutcome({
    status: "FAILED",
    error_code: "DATA_UNAVAILABLE",
    sanitized_error: "数据不可用：池内无共同行情",
    data: { data_mode: "UNAVAILABLE", reason: "池内无共同行情" },
  });
  assert.equal(outcome.status, "FAILED");
  assert.match(outcome.businessMessage, /数据不可用/);
});

test("信封 DEGRADED 如实呈现为业务降级（不冒充成功）", () => {
  const outcome = resolveSyncOutcome({
    status: "DEGRADED",
    sanitized_error: "硬风险 VETO：任何模型都不能解除",
    data: { research_run_id: "wlrun_x", status: "VETOED" },
  });
  assert.equal(outcome.status, "DEGRADED");
  assert.match(outcome.businessMessage, /硬风险 VETO/);
});

test("成功信封保持 SUCCEEDED 且不带业务错误", () => {
  const outcome = resolveSyncOutcome({
    status: "SUCCESS",
    data: { backtest_id: "wlbt_x", metrics: { sharpe_ratio: 0.92 } },
  });
  assert.equal(outcome.status, "SUCCEEDED");
  assert.equal(outcome.businessMessage, null);
});

test("裸对象响应（无信封 status）按成功处理", () => {
  const outcome = resolveSyncOutcome({ run_id: "run_1", requested: 3 });
  assert.equal(outcome.status, "SUCCEEDED");
});

test("信封 FAILED 且无 sanitized_error 时给出兜底中文说明", () => {
  const outcome = resolveSyncOutcome({ status: "FAILED", data: {} });
  assert.equal(outcome.status, "FAILED");
  assert.ok(outcome.businessMessage && outcome.businessMessage.length > 4);
});

test("data_mode=UNAVAILABLE 即使信封缺 FAILED 也判失败", () => {
  const outcome = resolveSyncOutcome({ status: "SUCCESS", data: { data_mode: "UNAVAILABLE", message: "尚无快照" } });
  assert.equal(outcome.status, "FAILED");
  assert.match(outcome.businessMessage, /尚无快照/);
});
