import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

async function loadTs(path, transform = (text) => text) {
  const source = transform(await readFile(new URL(path, import.meta.url), "utf8"));
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const labels = await loadTs("../src/domain/labels.ts");
const market = await loadTs("../src/domain/marketTime.ts");
const client = await loadTs("../src/api/client.ts", (text) => text.replace('"axios"', JSON.stringify(new URL("../node_modules/axios/index.js", import.meta.url).href)));

test("actual shadow confidence is a probability, never a centered score", () => {
  assert.equal(labels.formatConfidence(0.20016), "20.0%");
  assert.equal(labels.formatConfidence(0.0022), "0.2%");
  assert.equal(labels.formatConfidence(0), "0.0%");
  assert.equal(labels.formatConfidence(1), "100.0%");
  for (const value of [null, undefined, NaN, Infinity, -0.1, 1.1, "0.2"]) {
    assert.equal(labels.formatConfidence(value), "暂无数据");
  }
  assert.equal(labels.formatScore(null), "暂无数据");
});

test("offset timestamps display Shanghai time including UTC date rollover", () => {
  assert.equal(labels.formatDateTime("2026-10-16T18:02:17+09:00"), "2026-10-16 17:02");
  assert.equal(labels.formatDateTime("2026-10-18T16:30:00Z"), "2026-10-19 00:30");
  assert.equal(labels.formatDate("2026-10-18T16:30:00Z"), "2026-10-19");
  assert.equal(labels.formatDateTime("2026-10-16 18:02:17"), "2026-10-16 18:02（时区未确认）");
  assert.equal(labels.formatDate("2026-10-15"), "2026-10-15");
});

test("monitoring schedule is anchored to Shanghai weekdays and boundaries", () => {
  for (const instant of ["2026-10-19T01:10:00Z", "2026-10-19T05:30:00Z", "2026-10-19T07:05:00Z"]) {
    assert.equal(market.isTradingTime(new Date(instant)), true, instant);
  }
  for (const instant of ["2026-10-19T00:45:00Z", "2026-10-19T07:06:00Z", "2026-10-17T02:00:00Z"]) {
    assert.equal(market.isTradingTime(new Date(instant)), false, instant);
  }
});

test("network and missing-artifact errors use Chinese without leaking raw paths", () => {
  assert.equal(client.errorMessage({ isAxiosError: true, code: "ECONNABORTED" }), "请求超时，请稍后重试");
  assert.equal(client.errorMessage({ isAxiosError: true, response: { status: 404, data: { detail: "E:/private/report.csv" } } }), "暂无对应数据或研究产物");
  assert.equal(client.errorMessage({ isAxiosError: true, response: { status: 503, data: { detail: "数据源暂不可用" } } }), "数据源暂不可用");
  assert.equal(client.errorMessage(new Error("TypeError: internal exception")), "请求未完成，请稍后重试");
});

test("readiness and candidate names are understandable without replacing identifiers", () => {
  assert.equal(labels.getHealthStatusLabel("NOT_READY"), "尚未就绪");
  assert.equal(labels.getComponentLabel("finance_mcp"), "金融工具服务");
  assert.equal(labels.getCandidateLabel("dynamic_risk_07772020eeb8"), "动态风控 · 077720");
});

test("financial period remains distinct from the announcement date", () => {
  assert.equal(labels.getFinancialReportPeriod({report_period:"20260630", end_date:"20260630", ann_date:"20260815"}), "2026-06-30");
  assert.equal(labels.getFinancialReportPeriod({ann_date:"20260815"}), "暂无数据");
  assert.equal(labels.getFinancialReportPeriod({report_period:"20260230"}), "暂无数据");
  assert.equal(labels.getFinancialReportPeriod({report_period:"20260630", end_date:"20260331"}), "报告期字段冲突");
});

test("contradictory historical turnover cannot look like a verified zero", () => {
  assert.equal(labels.hasInconsistentTurnover({volume:0,amount:7537706.224}), true);
  assert.equal(labels.hasInconsistentTurnover({volume:1000,amount:12000}), false);
  assert.equal(labels.hasInconsistentTurnover({volume:0,amount:0}), false);
  assert.equal(labels.hasInconsistentTurnover({volume:null,amount:null}), false);
});

test("Walk-Forward folds identify their distinct training windows", () => {
  assert.equal(labels.getWalkForwardLabel({train_window_days:120,fold:0}), "120日 · 第1折");
  assert.notEqual(labels.getWalkForwardLabel({train_window_days:120,fold:0}), labels.getWalkForwardLabel({train_window_days:240,fold:0}));
});

test("polling starts at the idle interval and cannot restart after unmount during a request", async () => {
  const old = { setTimeout, clearTimeout, Date };
  const timers = [];
  globalThis.pollingTestHooks = { mount: [], dispose: [] };
  const hookUrl = `data:text/javascript,${encodeURIComponent('export function onMounted(fn){globalThis.pollingTestHooks.mount.push(fn)};export function onBeforeUnmount(fn){globalThis.pollingTestHooks.dispose.push(fn)}')}`;
  const marketSource = await readFile(new URL("../src/domain/marketTime.ts", import.meta.url), "utf8");
  const marketUrl = `data:text/javascript;base64,${Buffer.from(ts.transpileModule(marketSource, {compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText).toString("base64")}`;
  try {
    globalThis.Date = class extends old.Date { constructor(...args) { super(...(args.length ? args : ["2026-10-19T00:45:00Z"])); } };
    globalThis.setTimeout = (fn, delay) => { timers.push({fn, delay}); return timers.length; };
    globalThis.clearTimeout = () => {};
    const polling = await loadTs("../src/composables/usePolling.ts", (source) => source.replace('"vue"',JSON.stringify(hookUrl)).replaceAll('"../domain/marketTime"',JSON.stringify(marketUrl)));
    let finish;
    polling.usePolling(() => new Promise((resolve) => { finish = resolve; }), 100, 1000);
    globalThis.pollingTestHooks.mount[0]();
    assert.equal(timers[0].delay, 1000);
    const running = timers[0].fn();
    globalThis.pollingTestHooks.dispose[0]();
    finish();
    await running;
    assert.equal(timers.length, 1);
  } finally {
    Object.assign(globalThis, old);
    delete globalThis.pollingTestHooks;
  }
});
