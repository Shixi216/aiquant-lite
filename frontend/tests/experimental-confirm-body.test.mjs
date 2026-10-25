/**
 * 实验模式付费确认请求体回归测试（Codex 复审 P1-1 守卫）。
 * 复审复现：服务函数只发 {goal} → 后端 422 confirm_paid_calls=true。
 * 守卫：服务层请求体构造必须携带严格布尔 confirm_paid_calls。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

const { experimentalRunBody } = await import("../src/api/experimentalBody.ts");

test("用户确认后请求体携带布尔 true（复审第 1 项回归）", () => {
  const body = experimentalRunBody("比较核心四资产的历史风险", true);
  assert.deepEqual(body, {
    goal: "比较核心四资产的历史风险",
    confirm_paid_calls: true,
  });
  assert.equal(typeof body.confirm_paid_calls, "boolean");
});

test("未确认时请求体携带布尔 false（而非省略字段）", () => {
  const body = experimentalRunBody("研究目标", false);
  assert.equal(body.confirm_paid_calls, false);
  assert.equal(typeof body.confirm_paid_calls, "boolean");
});

test("字符串 true 视为未确认（false），防止伪装", () => {
  // @ts-expect-error 故意传错误类型，验证运行时防御
  const body = experimentalRunBody("研究目标", "true");
  assert.equal(body.confirm_paid_calls, false);
});

test("undefined 视为未确认（false）", () => {
  // @ts-expect-error 故意传错误类型，验证运行时防御
  const body = experimentalRunBody("研究目标", undefined);
  assert.equal(body.confirm_paid_calls, false);
});

test("goal 原样透传（后端契约校验 4-500 字）", () => {
  const goal = "研究中国多资产 ETF 池中风险较低的配置方案，比较三种策略，并生成风险报告。";
  const body = experimentalRunBody(goal, true);
  assert.equal(body.goal, goal);
});
