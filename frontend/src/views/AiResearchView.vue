<script setup lang="ts">
/**
 * AI 智能研究工作台（v3.3 三模型受控研究）。
 * 契约：/v1/wealth-lab/v3/research/*（routes_research.py）。
 * 原则：所有进度来自真实后端状态（POST 在途 / GET /runs/{id} 轮询），
 *       不模拟思考进度；付费调用必须用户显式确认；取消契约未提供则如实标注。
 */
import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from "vue";

import {
  approveResearchRun,
  fetchDecisionPacket,
  fetchResearchRoles,
  fetchResearchRun,
  runStableResearch,
  startExperimentalResearch,
} from "../api/aiResearch";
import { errorMessage } from "../api/client";
import { useWealthLabV2Store } from "../stores/wealthLabV2";
import { fmtPct, fmtRatio } from "../domain/wealthFormat";
import { getResearchRoleLabel, getResearchStatusLabel } from "../domain/labels";
import type { Ref } from "vue";
import echarts, { type EChartsCoreOption } from "../charts";
import { useECharts } from "../composables/useECharts";
import { useChartTheme, withAlpha } from "../domain/chartTheme";
import type {
  NumericVerification,
  ResearchExperiment,
  ResearchModeSpec,
  ResearchPlan,
  ResearchRoleSpec,
  ResearchRunRecord,
  ResearchRunResult,
  RiskCheckResult,
  WealthDecisionPacket,
} from "../types/aiResearch";

const wealthStore = useWealthLabV2Store();

/* ---------------- 表单状态 ---------------- */

type Mode = "STABLE_RESEARCH" | "EXPERIMENTAL_AI_RESEARCH";
const mode = ref<Mode>("STABLE_RESEARCH");
const goal = ref("");
const backtestId = ref(wealthStore.lastStrategyBacktest?.backtestId ?? "");
const allowReuse = ref(true);
const confirmPaidCalls = ref(false);
/** 实验模式：AI 规划（Flash）即首次付费调用，提交前必须显式确认（审计 P1-3 前端闸门）。 */
const confirmPlanningCall = ref(false);

const roles = ref<ResearchRolesData | null>(null);
const rolesError = ref<string | null>(null);
const rolesLoading = ref(false);

/* ---------------- 运行状态 ---------------- */

type Phase = "idle" | "planning" | "approval" | "executing" | "settled";
const phase = ref<Phase>("idle");
const runRecord = ref<ResearchRunRecord | null>(null);
const plan = ref<ResearchPlan | null>(null);
const result = ref<ResearchRunResult | null>(null);
const submitError = ref<string | null>(null);
const submitWarnings = ref<string[]>([]);
const pendingRunId = ref<string | null>(null);
const lastPolledAt = ref<string | null>(null);
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let pollSeq = 0;

const lastRunStoreKey = "wl-ai-research-last-run";
const lookupRunId = ref("");
const lookupError = ref<string | null>(null);

/* ---------------- 角色面板 ---------------- */

async function loadRoles() {
  rolesLoading.value = true;
  rolesError.value = null;
  try {
    roles.value = await fetchResearchRoles();
  } catch (err) {
    rolesError.value = errorMessage(err);
  } finally {
    rolesLoading.value = false;
  }
}
onMounted(() => {
  void loadRoles();
  void restoreLastRun();
});

const stableMode = computed<ResearchModeSpec | null>(() => roles.value?.modes.STABLE_RESEARCH ?? null);
const experimentalMode = computed<ResearchModeSpec | null>(() => roles.value?.modes.EXPERIMENTAL_AI_RESEARCH ?? null);
const roleList = computed<ResearchRoleSpec[]>(() => roles.value?.roles ?? []);
const budgetTotal = computed(() =>
  mode.value === "STABLE_RESEARCH" ? stableMode.value?.model_call_budget ?? 2 : experimentalMode.value?.model_call_budget ?? 3,
);

function roleNote(role: ResearchRoleSpec): string {
  return role.note || role.config_source;
}

/* ---------------- 发起研究 ---------------- */

const goalValid = computed(() => goal.value.trim().length >= 4 && goal.value.trim().length <= 500);
const backtestValid = computed(() => /^wlbt_[0-9a-f]{6,}$/.test(backtestId.value.trim()));
const canSubmit = computed(() => {
  if (phase.value === "planning" || phase.value === "executing") return false;
  if (mode.value === "EXPERIMENTAL_AI_RESEARCH") {
    return experimentalMode.value?.enabled === true && goalValid.value && confirmPlanningCall.value;
  }
  return stableMode.value?.enabled === true && backtestValid.value && confirmPaidCalls.value;
});

function switchMode(next: Mode) {
  mode.value = next;
  submitError.value = null;
  submitWarnings.value = [];
}

async function submitResearch() {
  submitError.value = null;
  submitWarnings.value = [];
  result.value = null;
  plan.value = null;
  runRecord.value = null;
  if (mode.value === "EXPERIMENTAL_AI_RESEARCH") {
    phase.value = "planning"; // POST 在途（AI 规划，最长 12 分钟）；期间不伪造进度
    try {
      const response = await startExperimentalResearch(
        goal.value.trim(),
        confirmPlanningCall.value,
      );
      phase.value = "approval";
      plan.value = response.plan ?? null;
      pendingRunId.value = response.research_run_id;
      runRecord.value = null;
      void refreshRunRecord(response.research_run_id);
      submitWarnings.value = ["AWAITING_USER_APPROVAL", "PLAN_NOT_AUTO_EXECUTED"];
    } catch (err) {
      phase.value = "idle";
      submitError.value = errorMessage(err);
    }
    return;
  }
  // STABLE：同步长链路（硬风控→Qwen→Pro→核验→决策包）
  phase.value = "executing";
  try {
    const response = await runStableResearch({
      backtest_id: backtestId.value.trim(),
      confirm_paid_calls: confirmPaidCalls.value,
      allow_reuse: allowReuse.value,
    });
    result.value = response;
    pendingRunId.value = response.research_run_id;
    await refreshRunRecord(response.research_run_id);
    phase.value = "settled";
  } catch (err) {
    const wrapped = err as Error & { payload?: Record<string, unknown> };
    if (wrapped.payload && typeof wrapped.payload.research_run_id === "string") {
      // VETOED / VERIFICATION_FAILED 以 envelope.data 返回：仍有完整记录可展示
      result.value = wrapped.payload as unknown as ResearchRunResult;
      pendingRunId.value = String(wrapped.payload.research_run_id);
      await refreshRunRecord(pendingRunId.value);
      phase.value = "settled";
    } else {
      phase.value = "idle";
      submitError.value = errorMessage(err);
    }
  }
}

/* ---------------- 确认与执行（实验模式） ---------------- */

async function approvePlan() {
  if (!pendingRunId.value) return;
  submitError.value = null;
  phase.value = "executing";
  const seq = ++pollSeq;
  // approve 为同步执行；并行轮询 GET /runs/{id} 展示真实阶段（不模拟）
  const pollPromise = pollRunRecord(pendingRunId.value, seq);
  try {
    const response = await approveResearchRun(pendingRunId.value);
    result.value = response;
    phase.value = "settled";
    await refreshRunRecord(response.research_run_id);
  } catch (err) {
    const wrapped = err as Error & { payload?: Record<string, unknown> };
    if (wrapped.payload && typeof wrapped.payload.research_run_id === "string") {
      result.value = wrapped.payload as unknown as ResearchRunResult;
      phase.value = "settled";
      await refreshRunRecord(String(wrapped.payload.research_run_id));
    } else {
      submitError.value = errorMessage(err);
      phase.value = "settled";
    }
  } finally {
    void pollPromise;
  }
}

async function refreshRunRecord(runId: string): Promise<void> {
  try {
    runRecord.value = await fetchResearchRun(runId);
    if (runRecord.value.plan_json && !plan.value) {
      try {
        plan.value = JSON.parse(runRecord.value.plan_json) as ResearchPlan;
      } catch { /* plan_json 损坏时如实留空 */ }
    }
    localStorage.setItem(lastRunStoreKey, JSON.stringify({ runId, at: new Date().toISOString() }));
  } catch { /* 详情读取失败不阻断主流程（结果已在本页） */ }
}

async function pollRunRecord(runId: string, seq: number): Promise<void> {
  while (pollSeq === seq) {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    if (pollSeq !== seq) return;
    try {
      const record = await fetchResearchRun(runId);
      if (pollSeq !== seq) return;
      runRecord.value = record;
      lastPolledAt.value = new Date().toTimeString().slice(0, 8);
      if (["COMPLETED", "VETOED", "VERIFICATION_FAILED", "FAILED"].includes(record.status)) return;
    } catch { /* 单次轮询失败继续重试（后端瞬时不可达） */ }
  }
}

onBeforeUnmount(() => {
  pollSeq += 1;
  if (pollTimer) clearTimeout(pollTimer);
});

/* ---------------- 历史恢复 / 查询 ---------------- */

async function restoreLastRun() {
  try {
    const raw = localStorage.getItem(lastRunStoreKey);
    if (!raw) return;
    const parsed = JSON.parse(raw) as { runId?: string };
    if (parsed.runId) {
      lookupRunId.value = parsed.runId;
      await lookupRun();
    }
  } catch { /* 忽略本地缓存损坏 */ }
}

async function lookupRun() {
  lookupError.value = null;
  const runId = lookupRunId.value.trim();
  if (!/^wlrun_[0-9a-f]{6,}$/.test(runId)) {
    lookupError.value = "run_id 格式形如 wlrun_xxxxxxxxxxxx";
    return;
  }
  try {
    const record = await fetchResearchRun(runId);
    runRecord.value = record;
    pendingRunId.value = record.research_run_id;
    plan.value = null;
    if (record.plan_json) {
      try {
        plan.value = JSON.parse(record.plan_json) as ResearchPlan;
      } catch { /* 如实留空 */ }
    }
    result.value = null;
    phase.value =
      record.status === "APPROVAL_PENDING" ? "approval"
      : record.status === "COMPLETED" || record.status === "VETOED" || record.status === "VERIFICATION_FAILED" || record.status === "FAILED" ? "settled"
      : "executing";
    if (phase.value === "executing") void pollRunRecord(record.research_run_id, ++pollSeq);
  } catch (err) {
    lookupError.value = errorMessage(err);
  }
}

/* ---------------- 决策包 ---------------- */

const packet = ref<WealthDecisionPacket | null>(null);
const packetLoading = ref(false);
const packetError = ref<string | null>(null);

async function loadPacket(packetId: string) {
  packetLoading.value = true;
  packetError.value = null;
  try {
    packet.value = await fetchDecisionPacket(packetId);
  } catch (err) {
    packetError.value = errorMessage(err);
  } finally {
    packetLoading.value = false;
  }
}

/* ---------------- 十步流程（映射真实 stage/status） ---------------- */

interface StepView { state: "done" | "active" | "pending" | "skipped" | "failed"; note: string }

const steps = computed<StepView[]>(() => {
  const record = runRecord.value;
  const res = result.value;
  const status = res?.status ?? record?.status ?? null;
  const stage = record?.stage ?? null;
  const experimental = (res?.mode ?? record?.mode) === "EXPERIMENTAL";
  const order = ["PLANNING", "VALIDATION", "APPROVAL", "BACKTESTING", "HARD_RISK_CHECK", "RESEARCH", "REVIEW", "VERIFICATION"];
  const reached = (target: string) => {
    if (status === "COMPLETED") return true;
    if (!stage) return false;
    // 终态/失败阶段视为已越过
    const terminal = ["COMPLETED", "VETOED", "VERIFICATION_FAILED", "FAILED"];
    if (terminal.includes(stage)) return true;
    return order.indexOf(stage) >= order.indexOf(target) || (target === "APPROVAL" && stage === "APPROVAL_PENDING");
  };
  const active = (target: string) => {
    if (!stage) return false;
    if (target === "APPROVAL") return stage === "APPROVAL_PENDING" || record?.status === "APPROVAL_PENDING";
    return stage === target || (target === "HARD_RISK_CHECK" && status === "VETOED" && !stage);
  };
  const failedHere = (target: string) => {
    if (status === "VETOED" && target === "HARD_RISK_CHECK") return true;
    if (status === "VERIFICATION_FAILED" && target === "VERIFICATION") return true;
    if (status === "FAILED") {
      if (target === "HARD_RISK_CHECK" && (record?.error_reason ?? "").includes("VETO")) return true;
      if (target === "VERIFICATION" && (record?.error_reason ?? "").includes("verification")) return true;
      // 其余失败归因到当前阶段
      return stage === target;
    }
    return false;
  };
  return [
    { state: (goal.value.trim() || backtestId.value.trim() || record?.goal || res) ? "done" : "pending", note: "" },
    experimental
      ? { state: res?.plan || record?.plan_json ? "done" : active("PLANNING") ? "active" : failedHere("PLANNING") ? "failed" : "pending", note: "" }
      : { state: "skipped", note: "稳定模式基于既有回测证据，无独立规划调用" },
    experimental
      ? { state: reached("VALIDATION") ? "done" : failedHere("VALIDATION") ? "failed" : "pending", note: "白名单校验：资产池/策略/区间" }
      : { state: res || record?.backtest_id ? "done" : active("BACKTESTING") ? "active" : "pending", note: "加载既有回测证据" },
    experimental
      ? { state: record?.status === "APPROVAL_PENDING" || phase.value === "approval" ? "active" : reached("APPROVAL") && phase.value !== "approval" ? "done" : "pending", note: "等待用户确认后才开始实验" }
      : { state: "done", note: "提交即确认（含付费调用确认）" },
    { state: reached("BACKTESTING") || (res?.experiments?.length ?? 0) > 0 ? "done" : active("BACKTESTING") ? "active" : "pending", note: "" },
    { state: (res?.experiments?.length ?? 0) > 0 ? "done" : "pending", note: "" },
    { state: res?.risk_check || record?.status === "VETOED" ? "done" : active("HARD_RISK_CHECK") ? "active" : failedHere("HARD_RISK_CHECK") ? "failed" : "pending", note: "" },
    { state: res?.research ? "done" : active("RESEARCH") ? "active" : failedHere("RESEARCH") ? "failed" : "pending", note: "" },
    { state: res?.review ? "done" : active("REVIEW") ? "active" : failedHere("REVIEW") ? "failed" : "pending", note: "" },
    { state: status === "COMPLETED" ? "done" : status === "VERIFICATION_FAILED" ? "failed" : active("VERIFICATION") ? "active" : "pending", note: "" },
  ];
});

const stepMeta = [
  { title: "研究目标", desc: "自然语言目标 / 既有回测选择" },
  { title: "AI 规划方案", desc: "DeepSeek Flash 结构化规划（实验模式）" },
  { title: "参数及数据校验", desc: "白名单校验 / 回测证据加载（确定性）" },
  { title: "用户确认", desc: "确认后才开始真实实验" },
  { title: "真实策略实验", desc: "v3 引擎逐池回测（≤3 组）" },
  { title: "实验结果对比", desc: "收益/回撤/风险调整指标对比" },
  { title: "确定性风险 VETO", desc: "硬风控，任何模型不可解除" },
  { title: "AI 研究结论", desc: "Qwen 结构化研究摘要" },
  { title: "AI 风险复核", desc: "DeepSeek Pro 独立审阅" },
  { title: "数值核验与决策包", desc: "确定性核验通过才产出决策包" },
];

/* ---------------- 展示辅助 ---------------- */

const experiments = computed<ResearchExperiment[]>(() => result.value?.experiments ?? []);
const bestExperimentId = computed(() => {
  const list = experiments.value;
  if (!list.length) return null;
  return list.reduce((best, item) =>
    (item.metrics?.sharpe_ratio ?? -9) > (best.metrics?.sharpe_ratio ?? -9) ? item : best).backtest_id;
});

const riskCheck = computed<RiskCheckResult | null>(() => result.value?.risk_check ?? null);
const researchText = computed<string>(() => prettyPayload(result.value?.research));
const reviewText = computed<string>(() => prettyPayload(result.value?.review));
const verification = computed<NumericVerification | null>(() => result.value?.numeric_verification ?? null);

function prettyPayload(payload: unknown): string {
  if (payload === null || payload === undefined) return "";
  if (typeof payload === "string") return payload;
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

const metricColumns: { key: string; label: string; fmt: (v: number) => string }[] = [
  { key: "total_return", label: "累计收益", fmt: (v) => fmtPct(v) },
  { key: "annual_return", label: "年化", fmt: (v) => fmtPct(v) },
  { key: "max_drawdown", label: "最大回撤", fmt: (v) => fmtPct(v) },
  { key: "sharpe_ratio", label: "夏普", fmt: (v) => fmtRatio(v) },
  { key: "sortino_ratio", label: "Sortino", fmt: (v) => fmtRatio(v) },
  { key: "calmar_ratio", label: "卡玛", fmt: (v) => fmtRatio(v) },
  { key: "annual_volatility", label: "年化波动", fmt: (v) => fmtPct(v, 2, false) },
  { key: "trading_days", label: "交易日", fmt: (v) => String(v) },
];

const modelCalls = computed(() => runRecord.value?.model_calls ?? []);

/* ---------------- 实验与调用可视化（真实数据） ---------------- */

const expScatterEl = ref<HTMLElement | null>(null);
const expScatterChart = useECharts(expScatterEl as Ref<HTMLElement | null>);
const latencyBarEl = ref<HTMLElement | null>(null);
const latencyBarChart = useECharts(latencyBarEl as Ref<HTMLElement | null>);
const chartTheme = useChartTheme();

/** 实验风险收益散点：横轴年化波动率、纵轴年化收益（真实回测 metrics）。 */
const expScatterOption = computed<EChartsCoreOption | null>(() => {
  const list = experiments.value;
  if (list.length < 2) return null;
  return {
    animationDuration: 450,
    tooltip: {
      trigger: "item",
      backgroundColor: chartTheme.value.tooltipBg,
      borderColor: chartTheme.value.border,
      textStyle: { color: chartTheme.value.tooltipText, fontSize: 12 },
      formatter: (p: { name: string; value: number[] }) =>
        `<b>${p.name}</b><br/>年化波动率 ${p.value[0]}%<br/>年化收益 ${p.value[1]}%<br/>夏普比率 ${p.value[2]}`,
    },
    grid: { left: 60, right: 24, top: 22, bottom: 40 },
    xAxis: {
      name: "年化波动率",
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { color: chartTheme.value.dim, fontSize: 11 },
      axisLabel: { color: chartTheme.value.dim, formatter: "{value}%" },
      splitLine: { lineStyle: { color: chartTheme.value.grid } },
      scale: true,
    },
    yAxis: {
      name: "年化收益",
      nameTextStyle: { color: chartTheme.value.dim, fontSize: 11 },
      axisLabel: { color: chartTheme.value.dim, formatter: "{value}%" },
      splitLine: { lineStyle: { color: chartTheme.value.grid } },
      scale: true,
    },
    series: [
      {
        type: "scatter",
        symbolSize: (value: number[]) => Math.max(18, Math.min(40, 16 + value[2] * 12)),
        label: { show: true, position: "top", color: chartTheme.value.text, fontSize: 11, formatter: (p: { name: string }) => p.name },
        itemStyle: { color: withAlpha(chartTheme.value.accent, 0.78), borderColor: chartTheme.value.accent, borderWidth: 1 },
        data: list.map((experiment) => ({
          name: experiment.strategy_id,
          value: [
            Number(((experiment.metrics?.annual_volatility ?? 0) * 100).toFixed(2)),
            Number(((experiment.metrics?.annual_return ?? 0) * 100).toFixed(2)),
            Number((experiment.metrics?.sharpe_ratio ?? 0).toFixed(2)),
          ],
        })),
      },
    ],
  } satisfies EChartsCoreOption;
});

/** 模型调用耗时条形（真实审计 latency_ms）。 */
const latencyBarOption = computed<EChartsCoreOption | null>(() => {
  const calls = modelCalls.value.filter((call) => typeof call.latency_ms === "number");
  if (!calls.length) return null;
  return {
    animationDuration: 450,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: chartTheme.value.tooltipBg,
      borderColor: chartTheme.value.border,
      textStyle: { color: chartTheme.value.tooltipText, fontSize: 12 },
      valueFormatter: (v: number) => `${(v / 1000).toFixed(1)} 秒`,
    },
    grid: { left: 150, right: 40, top: 10, bottom: 24 },
    xAxis: {
      type: "value",
      axisLabel: { color: chartTheme.value.dim, fontSize: 10, formatter: (v: number) => `${(v / 1000).toFixed(0)}s` },
      splitLine: { lineStyle: { color: chartTheme.value.grid } },
    },
    yAxis: {
      type: "category",
      data: calls.map((call) => `${getResearchRoleLabel(call.agent_role)} · ${call.display_model_name}`),
      axisLabel: { color: chartTheme.value.text, fontSize: 11 },
      axisLine: { lineStyle: { color: chartTheme.value.axisLine } },
    },
    series: [
      {
        type: "bar",
        barWidth: 14,
        itemStyle: { borderRadius: [0, 5, 5, 0], color: withAlpha(chartTheme.value.accent, 0.8) },
        data: calls.map((call) => Math.round(call.latency_ms as number)),
      },
    ],
  } satisfies EChartsCoreOption;
});

watchEffect(() => {
  const opt = expScatterOption.value;
  if (opt) expScatterChart.setOption(opt, true);
});
watchEffect(() => {
  const opt = latencyBarOption.value;
  if (opt) latencyBarChart.setOption(opt, true);
});

const statusBadgeCls = computed(() => {
  const status = runRecord.value?.status ?? result.value?.status;
  switch (status) {
    case "COMPLETED": return "badge ok";
    case "VETOED": case "FAILED": case "VERIFICATION_FAILED": return "badge veto";
    case "APPROVAL_PENDING": return "badge warn";
    default: return "badge action";
  }
});

function fmtTime(iso: string | null | undefined): string {
  return iso ? iso.replace("T", " ").slice(0, 19) : "—";
}
</script>

<template>
  <div class="ai-research">
    <p class="page-sub">
      v3.3 三模型受控研究：规划（Flash）→ 白名单校验 → 用户确认 → 真实 v3 实验 → 确定性硬风控 VETO →
      Qwen 研究 → DeepSeek Pro 复核 → 数值核验 → 决策包。所有进度来自后端真实状态；付费模型调用需显式确认。
    </p>

    <div class="layout">
      <!-- 左列：发起与控制 -->
      <div class="col-form">
        <section class="panel">
          <h3>发起研究</h3>
          <div class="field">
            <span class="field-label">运行模式</span>
            <div class="seg mode-seg">
              <button
                type="button"
                :class="{ active: mode === 'STABLE_RESEARCH' }"
                title="稳定研究：基于既有回测证据，2 次模型调用（后端模式 STABLE_RESEARCH）"
                @click="switchMode('STABLE_RESEARCH')"
              >
                稳定研究<span class="mode-tag">推荐</span>
              </button>
              <button
                type="button"
                :class="{ active: mode === 'EXPERIMENTAL_AI_RESEARCH' }"
                :disabled="experimentalMode !== null && !experimentalMode.enabled"
                :title="experimentalMode && !experimentalMode.enabled ? `未启用（需后端设置 ${experimentalMode.flag ?? 'WEALTH_LAB_EXPERIMENTAL_AI'}）` : '实验模式：AI 规划 + 多组实验（3 次调用预算；后端模式 EXPERIMENTAL_AI_RESEARCH）'"
                @click="switchMode('EXPERIMENTAL_AI_RESEARCH')"
              >实验性研究</button>
            </div>
            <p v-if="experimentalMode && !experimentalMode.enabled" class="hint warn-text">
              实验模式未启用（需后端设置 {{ experimentalMode.flag ?? 'WEALTH_LAB_EXPERIMENTAL_AI' }}）；按契约后端将拒绝（403），此处先行禁用。
            </p>
          </div>

          <label class="field">
            <span class="field-label">研究目标（自然语言，4–500 字）</span>
            <textarea
              v-model="goal"
              class="goal-input"
              rows="4"
              placeholder="例：研究中国多资产 ETF 池中风险较低的配置方案，比较三种策略，并生成风险报告。"
            ></textarea>
          </label>

          <template v-if="mode === 'STABLE_RESEARCH'">
            <label class="field">
              <span class="field-label">既有回测编号（稳定研究基于已存档的回测结果）</span>
              <div class="row">
                <input v-model="backtestId" type="text" placeholder="wlbt_xxxxxxxxxxxx" />
                <button
                  v-if="wealthStore.lastStrategyBacktest"
                  class="mini" type="button"
                  :title="`填入最近回测 ${wealthStore.lastStrategyBacktest.backtestId}（${wealthStore.lastStrategyBacktest.strategyId} · ${wealthStore.lastStrategyBacktest.universeId}）`"
                  @click="backtestId = wealthStore.lastStrategyBacktest.backtestId"
                >用最近回测</button>
              </div>
              <span class="hint">可在「策略工厂」运行回测后自动关联，或在「数据中心 → 任务记录」查看历史 backtest_id。</span>
            </label>
            <label class="check-field" title="对应后端参数 allow_reuse">
              <input v-model="allowReuse" type="checkbox" />
              <span>复用既有结论：同一回测（相同数据与参数）已完成研究时直接采用，不重复调用模型</span>
            </label>
            <label class="check-field paid" title="对应后端参数 confirm_paid_calls">
              <input v-model="confirmPaidCalls" type="checkbox" />
              <span>我确认本次研究包含 <b>{{ stableMode?.model_call_budget ?? 2 }} 次真实付费模型调用</b>（AI 研究结论 + 独立风险复核）</span>
            </label>
          </template>

          <template v-else>
            <div class="field">
              <span class="field-label">规划说明</span>
              <p class="hint">
                提交后由 DeepSeek Flash 产出结构化计划（资产池/策略候选/区间/约束），经白名单校验后<b>等待你确认</b>才会执行真实实验。
                资产池与区间由 AI 在后端白名单内规划，不做前端预设。
              </p>
            </div>
            <label class="check-field paid">
              <input v-model="confirmPlanningCall" type="checkbox" />
              <span>我确认提交即触发 <b>1 次真实付费模型调用</b>（DeepSeek Flash 规划）；该调用发生在方案确认之前，不可撤销</span>
            </label>
          </template>

          <div class="field">
            <span class="field-label">模型调用预算（后端契约固定）</span>
            <span class="badge">{{ mode === 'STABLE_RESEARCH' ? '稳定研究' : '实验性研究' }} · 最多 {{ budgetTotal }} 次付费调用</span>
          </div>

          <button class="mini primary run-btn" type="button" :disabled="!canSubmit" @click="submitResearch">
            {{ phase === "planning" ? "AI 规划中（真实请求进行中）…" : phase === "executing" ? "后端执行中…" : mode === "STABLE_RESEARCH" ? "发起稳定研究" : "提交目标，开始 AI 规划" }}
          </button>
          <p v-if="submitError" class="error-box" role="alert">{{ submitError }}</p>
          <p v-for="warning in submitWarnings" :key="warning" class="hint warn-text">※ {{ warning }}</p>

          <div class="field lookup">
            <span class="field-label">按运行编号查询历史研究（编号形如 wlrun_…，可在审计记录中找到）</span>
            <div class="row">
              <input v-model="lookupRunId" type="text" placeholder="wlrun_xxxxxxxxxxxx" />
              <button class="mini" type="button" @click="lookupRun">查询</button>
            </div>
            <span v-if="lookupError" class="hint warn-text">{{ lookupError }}</span>
          </div>
        </section>

        <section class="panel">
          <h3>
            模型角色（后端声明）
            <button class="mini" type="button" @click="loadRoles">{{ rolesLoading ? "刷新中…" : "刷新" }}</button>
          </h3>
          <div v-if="rolesError" class="error-box">{{ rolesError }}</div>
          <div v-for="role in roleList" :key="role.role" class="role-card">
            <div class="role-head">
              <strong>{{ getResearchRoleLabel(role.role) }}<small class="role-en">{{ role.role }}</small></strong>
              <span class="badge" :class="role.available ? 'ok' : 'warn'">{{ role.available ? "可用" : "暂不可用" }}</span>
            </div>
            <p class="role-model">{{ role.display_model_name }}<small v-if="role.actual_model_id"> · {{ role.actual_model_id }}</small></p>
            <p class="role-note">{{ roleNote(role) }}</p>
          </div>
          <p v-if="roleList.some((r) => !r.available)" class="hint warn-text">
            ※ 存在暂不可用模型：对应阶段将由后端明确标记并降级或终止，不会猜测替代模型。
          </p>
        </section>
      </div>

      <!-- 右列：流程与结果 -->
      <div class="col-main">
        <section class="panel">
          <h3>
            研究流程
            <span v-if="runRecord" class="h3-extra">
              {{ runRecord.research_run_id }} · {{ runRecord.mode }} · 预算 {{ runRecord.model_calls_used }}/{{ runRecord.model_call_budget }}
            </span>
            <span v-if="runRecord" :class="statusBadgeCls">{{ getResearchStatusLabel(runRecord.status) }}</span>
          </h3>

          <div v-if="phase === 'planning'" class="phase-note" role="status">
            AI 规划请求进行中（真实同步调用，最长 12 分钟）——完成后自动展示结构化方案；期间不显示任何模拟进度。
          </div>
          <div v-else-if="phase === 'executing'" class="phase-note" role="status">
            后端执行中（实验/风控/模型调用为真实同步链路，Qwen 阶段可能超过一分钟）；页面不阻塞，
            下方阶段来自每 2.5 秒的真实状态轮询<template v-if="lastPolledAt">（最近 {{ lastPolledAt }}）</template>。
            可离开本页稍后按 run_id 查询恢复。
          </div>
          <div v-if="!runRecord && phase === 'idle'" class="empty">发起或查询一次研究后，此处显示十步流程与全部证据。</div>

          <ol v-if="runRecord || result" class="steps">
            <li v-for="(step, index) in steps" :key="index" :class="`step ${step.state}`">
              <span class="step-no">{{ index + 1 }}</span>
              <span class="step-body">
                <b>{{ stepMeta[index]!.title }}</b>
                <small v-if="step.state === 'skipped'">— {{ step.note }}</small>
                <small v-else-if="step.state === 'active'">— 进行中（后端真实状态）</small>
                <small v-else-if="step.state === 'failed'">— 未通过（原因见下方说明）</small>
                <small v-else-if="step.note && step.state === 'pending'">— {{ step.note }}</small>
              </span>
            </li>
          </ol>

          <p v-if="runRecord?.status === 'COMPLETED'" class="hint completed-note">
            ※ 「研究完成」表示流水线结束并已产出决策包；结论有效性以确定性风控与数值核验为准，独立复核结论见下方原文。
          </p>
          <div v-if="runRecord?.error_reason" class="error-box" role="alert">
            后端记录失败原因：{{ runRecord.error_reason }}
          </div>
          <div v-if="runRecord?.status === 'VETOED' || result?.status === 'VETOED'" class="veto-note" role="alert">
            硬风险 VETO：确定性风控否决，任何模型都不能解除（HARD_VETO_NOT_LIFTABLE_BY_AI）。未生成决策包。
          </div>
          <div v-if="result?.status === 'VERIFICATION_FAILED'" class="veto-note" role="alert">
            数值核验未通过：AI 结论含无法核验的数字，按规则终止，不生成决策包。
          </div>

          <label class="check-field cancel-field" title="v3.3 契约未提供 research run 取消端点；同步执行由后端 720 秒总超时兜底">
            <input type="checkbox" disabled />
            <span>取消任务（v3.3 契约未提供研究运行取消端点；后端总超时 720s 兜底）</span>
          </label>
        </section>

        <!-- 2. 规划方案 -->
        <section v-if="plan" class="panel">
          <h3>
            AI 规划方案（DeepSeek Flash）
            <span class="h3-extra">白名单校验通过</span>
          </h3>
          <div class="plan-grid">
            <div><span>研究目标</span><b>{{ plan.research_goal }}</b></div>
            <div><span>资产池（AI 规划）</span><b>{{ getAssetCategoryLabelV3(plan.universe_id) === plan.universe_id ? plan.universe_id : getAssetCategoryLabelV3(plan.universe_id) + '（' + plan.universe_id + '）' }}</b></div>
            <div><span>历史区间</span><b>{{ plan.start_date }} ~ {{ plan.end_date }}</b></div>
            <div><span>基准</span><b>{{ plan.benchmark ?? "EQUAL_WEIGHT" }}</b></div>
          </div>
          <div class="plan-list">
            <div v-if="plan.strategy_candidates?.length">
              <span>策略候选</span>
              <span v-for="candidate in plan.strategy_candidates" :key="candidate" class="chip static">{{ candidate }}</span>
            </div>
            <div v-if="plan.constraints?.length">
              <span>约束</span>
              <span v-for="constraint in plan.constraints" :key="constraint" class="chip static">{{ constraint }}</span>
            </div>
            <div v-if="plan.hypotheses?.length">
              <span>假设</span>
              <span v-for="hypothesis in plan.hypotheses" :key="hypothesis" class="chip static">{{ hypothesis }}</span>
            </div>
          </div>
          <p class="hint">
            v3.3 契约的确认端点不接受参数修改：方案经白名单校验后原样执行或放弃；如需调整，请修改目标文字后重新发起规划。
          </p>
          <button
            v-if="runRecord?.status === 'APPROVAL_PENDING' && phase === 'approval'"
            class="mini primary approve-btn" type="button" @click="approvePlan"
          >确认方案并开始真实实验（消耗剩余 {{ (runRecord.model_call_budget ?? 3) - (runRecord.model_calls_used ?? 0) }} 次调用预算）</button>
        </section>

        <!-- 5/6. 实验对比 -->
        <section v-if="experiments.length" class="panel">
          <h3>
            实验结果对比（真实 v3 回测）
            <span class="h3-extra">每组均为独立 backtest_id，可追溯</span>
          </h3>
          <div class="table-scroll">
            <table class="data">
              <thead>
                <tr>
                  <th>策略</th><th>backtest_id</th>
                  <th v-for="column in metricColumns" :key="column.key" class="num">{{ column.label }}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="experiment in experiments" :key="experiment.backtest_id">
                  <td><strong>{{ experiment.strategy_id }}</strong></td>
                  <td><code class="mono">{{ experiment.backtest_id }}</code></td>
                  <td v-for="column in metricColumns" :key="column.key" class="num">
                    {{ experiment.metrics?.[column.key] !== undefined ? column.fmt(experiment.metrics[column.key]!) : "—" }}
                  </td>
                  <td><span v-if="experiment.backtest_id === bestExperimentId" class="badge action">最优（夏普）</span></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="viz-caption">
            <strong>实验风险收益分布</strong>
            <span>横轴年化波动率、纵轴年化收益，点大小为夏普比率（真实回测指标）</span>
          </div>
          <div v-show="experiments.length >= 2" ref="expScatterEl" class="viz exp-scatter-viz"></div>
        </section>

        <!-- 7. 风险核查 -->
        <section v-if="riskCheck" class="panel">
          <h3>
            确定性风险核查
            <span class="badge" :class="riskCheck.verdict === 'VETO' ? 'veto' : 'ok'">{{ riskCheck.verdict }}</span>
          </h3>
          <pre v-if="Object.keys(riskCheck).length" class="payload">{{ prettyPayload(riskCheck) }}</pre>
        </section>

        <!-- 8/9. AI 结论与复核 -->
        <section v-if="researchText" class="panel">
          <h3>AI 研究结论（Qwen 3.8 Max 0902）<span class="h3-extra">仅基于确定性计算事实；数字受第 10 步核验</span></h3>
          <pre class="payload">{{ researchText }}</pre>
        </section>
        <section v-if="reviewText" class="panel">
          <h3>AI 风险复核（DeepSeek V4 Pro）<span class="h3-extra">独立审阅；硬风控结论不因复核改变</span></h3>
          <pre class="payload">{{ reviewText }}</pre>
        </section>

        <!-- 10a. 数值核验 -->
        <section v-if="verification" class="panel">
          <h3>
            数值核验（确定性）
            <span class="badge" :class="verification.passed ? 'ok' : 'veto'">{{ verification.passed ? "通过" : "未通过" }}</span>
          </h3>
          <p v-if="verification.unsupported?.length" class="hint warn-text">
            无法核验的表述 {{ verification.unsupported.length }} 条（已按规则终止）：
          </p>
          <ul v-if="verification.unsupported?.length" class="unsupported-list">
            <li v-for="item in verification.unsupported" :key="item">{{ item }}</li>
          </ul>
        </section>

        <!-- 10b. 决策包 -->
        <section v-if="result?.decision_packet_id || runRecord?.decision_packet_id" class="panel">
          <h3>
            研究决策包（仅限研究用途，不构成投资指令）
            <code class="mono">{{ result?.decision_packet_id ?? runRecord?.decision_packet_id }}</code>
          </h3>
          <button
            v-if="!packet"
            class="mini primary" type="button"
            :disabled="packetLoading"
            @click="loadPacket(String(result?.decision_packet_id ?? runRecord?.decision_packet_id))"
          >{{ packetLoading ? "读取中…" : "加载决策包详情" }}</button>
          <div v-if="packetError" class="error-box">{{ packetError }}</div>
          <div v-if="packet" class="plan-grid">
            <div><span>策略</span><b>{{ packet.strategy_id }} v{{ packet.strategy_version ?? "—" }}</b></div>
            <div><span>backtest_id</span><b><code class="mono">{{ packet.backtest_id ?? "—" }}</code></b></div>
            <div><span>回测窗口</span><b>{{ (packet.backtest_window as any)?.effective_start_date ?? "—" }} ~ {{ (packet.backtest_window as any)?.end_date ?? "—" }}</b></div>
            <div><span>数据源</span><b>{{ (packet.data_sources ?? []).join(" · ") || "—" }}</b></div>
          </div>
          <div v-if="packet?.weights && Object.keys(packet.weights).length" class="plan-list">
            <div>
              <span>目标权重</span>
              <span v-for="(weight, symbol) in packet.weights" :key="symbol" class="chip static">
                {{ symbol }} {{ fmtPct(weight, 1, false) }}
              </span>
            </div>
          </div>
          <pre v-if="packet" class="payload">{{ prettyPayload(packet) }}</pre>
        </section>

        <!-- 模型调用审计 -->
        <section v-if="modelCalls.length" class="panel">
          <h3>
            模型调用审计
            <span class="h3-extra">{{ modelCalls.length }} 次 · 全部落库可追溯</span>
          </h3>
          <div class="viz-caption">
            <strong>模型调用耗时</strong>
            <span>每次真实调用的端到端延迟（审计记录）</span>
          </div>
          <div ref="latencyBarEl" class="viz latency-viz"></div>
          <div class="table-scroll">
            <table class="data">
              <thead>
                <tr><th>角色</th><th>模型</th><th>状态</th><th class="num">延迟</th><th class="num">输入/输出 tokens</th><th>时间</th><th>错误</th></tr>
              </thead>
              <tbody>
                <tr v-for="call in modelCalls" :key="call.call_id">
                  <td><strong>{{ call.agent_role }}</strong></td>
                  <td>{{ call.display_model_name }}<small v-if="call.actual_model_id" class="run-path">{{ call.actual_model_id }}</small></td>
                  <td><span class="badge" :class="call.status === 'SUCCESS' ? 'ok' : 'veto'">{{ call.status }}</span></td>
                  <td class="num">{{ call.latency_ms != null ? `${(call.latency_ms / 1000).toFixed(1)}s` : "—" }}</td>
                  <td class="num">{{ call.input_tokens ?? "—" }} / {{ call.output_tokens ?? "—" }}</td>
                  <td class="num">{{ fmtTime(call.started_at) }}</td>
                  <td class="error-text">{{ call.error_reason ?? "—" }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 400px 1fr; gap: 16px; align-items: start; }
.col-form, .col-main { display: flex; flex-direction: column; min-width: 0; }
.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 13px; }
.field-label { font-size: 12.5px; color: var(--text-dim); }
.row { display: flex; gap: 8px; align-items: center; }
.row input { flex: 1; min-width: 0; }
.goal-input { width: 100%; resize: vertical; min-height: 88px; font-family: inherit; color: var(--text); background: var(--control-bg); border: 1px solid var(--border); border-radius: var(--radius-ctrl); padding: 9px 11px; font-size: 13px; }
.goal-input:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 3px var(--accent-ring); }
.mode-seg { display: flex; }
.mode-seg button { padding: 7px 12px; font-size: 11.5px; }
.mode-tag { margin-left: 5px; font-size: 9.5px; opacity: 0.75; }
.check-field { display: flex; align-items: flex-start; gap: 8px; font-size: 12.5px; color: var(--text-dim); margin-bottom: 11px; cursor: pointer; line-height: 1.6; }
.check-field input { accent-color: var(--accent); margin-top: 3px; }
.check-field.paid span { color: var(--warn); }
.run-btn { width: 100%; padding: 10px 0; font-size: 13.5px; margin-top: 2px; }
.lookup { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border-soft); }
.hint { margin: 0; font-size: 11.5px; color: var(--text-faint); line-height: 1.6; }
.warn-text { color: var(--warn); }
.role-card { border: 1px solid var(--border); border-radius: var(--radius-ctrl); padding: 9px 12px; margin-bottom: 8px; background: var(--bg-panel-2); }
.role-head { display: flex; justify-content: space-between; align-items: center; }
.role-head strong { font-size: 12.5px; }
.role-en { margin-left: 6px; font-size: 10px; color: var(--text-faint); font-weight: 400; }
.role-model { margin: 4px 0 0; font-size: 12.5px; color: var(--text); }
.role-model small { color: var(--text-faint); }
.role-note { margin: 3px 0 0; font-size: 11px; color: var(--text-faint); line-height: 1.55; }
.completed-note { margin: 8px 0 0; }
.phase-note { padding: 10px 14px; border: 1px solid var(--accent-ring); border-radius: var(--radius-ctrl); background: var(--accent-softer); color: var(--text); font-size: 12.5px; line-height: 1.7; margin-bottom: 12px; }
.steps { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
.step { display: flex; align-items: flex-start; gap: 9px; padding: 8px 11px; border: 1px solid var(--border); border-radius: var(--radius-ctrl); background: var(--bg-panel-2); }
.step-no { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 50%; flex-shrink: 0; font-size: 11px; font-weight: 700; background: var(--bg-hover); color: var(--text-dim); }
.step-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.step-body b { font-size: 12.5px; }
.step-body small { font-size: 11px; color: var(--text-dim); }
.step.done { border-color: color-mix(in srgb, var(--down) 40%, transparent); }
.step.done .step-no { background: color-mix(in srgb, var(--down) 16%, transparent); color: var(--down); }
.step.active { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-ring); }
.step.active .step-no { background: var(--accent-grad); color: var(--accent-contrast); }
.step.failed { border-color: var(--veto); }
.step.failed .step-no { background: color-mix(in srgb, var(--veto) 14%, transparent); color: var(--veto); }
.step.skipped { opacity: 0.6; border-style: dashed; }
.veto-note { margin-top: 12px; padding: 10px 14px; border: 1px solid color-mix(in srgb, var(--veto) 45%, transparent); border-radius: var(--radius-ctrl); background: color-mix(in srgb, var(--veto) 7%, transparent); color: var(--veto); font-size: 12.5px; line-height: 1.7; }
.cancel-field { margin-top: 12px; opacity: 0.65; cursor: not-allowed; }
.plan-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px 16px; margin-bottom: 10px; }
.plan-grid > div { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.plan-grid span { font-size: 11.5px; color: var(--text-dim); }
.plan-grid b { font-size: 13px; word-break: break-all; }
.plan-list > div { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 7px; font-size: 12px; }
.plan-list > div > span:first-child { color: var(--text-dim); margin-right: 4px; }
.chip.static { cursor: default; }
.approve-btn { margin-top: 10px; padding: 9px 16px; }
.payload { margin: 0; font-size: 11.5px; max-height: 340px; overflow: auto; background: var(--control-bg); border: 1px solid var(--border-soft); border-radius: var(--radius-ctrl); padding: 10px 12px; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
.unsupported-list { margin: 6px 0 0; padding-left: 18px; font-size: 12px; color: var(--warn); line-height: 1.7; }
.mono { font-family: Consolas, monospace; font-size: 11.5px; color: var(--accent-strong); }
.run-path { display: block; font-size: 10.5px; color: var(--text-faint); }
.error-text { color: var(--veto); font-size: 11.5px; }
.viz-caption { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin: 12px 0 4px; }
.viz-caption strong { font-size: 12.5px; }
.viz-caption span { font-size: 11px; color: var(--text-dim); }
.exp-scatter-viz { height: 300px; }
.latency-viz { height: 150px; }
@media (max-width: 1200px) {
  .layout { grid-template-columns: 1fr; }
  .steps { grid-template-columns: 1fr; }
}
</style>
