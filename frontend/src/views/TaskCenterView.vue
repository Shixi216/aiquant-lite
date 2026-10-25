<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { fetchWealthMeta } from "../api/wealthLab";
import { fetchV3Quality } from "../api/wealthLabV3";
import {
  buildPayload,
  cancelJob,
  executeOp,
  fetchJobLogs,
  fetchJobs,
  fetchTaskCenterStatus,
  JOB_OPERATION_LABELS,
  TASK_OPS,
  validateOp,
  type JobPublic,
  type TaskOpDef,
  type TaskRunRecord,
} from "../api/taskCenter";
import { errorMessage } from "../api/client";
import { usePolling } from "../composables/usePolling";
import type { MetaData } from "../types/wealthLab";
import type { QualityData } from "../types/wealthLabV3";

/* ---------------- 状态面板 ---------------- */

const status = ref<Awaited<ReturnType<typeof fetchTaskCenterStatus>> | null>(null);
const statusLoading = ref(false);
const statusError = ref<string | null>(null);
const wealthMeta = ref<MetaData | null>(null);
const wealthMetaSource = ref<string>("live");
const quality = ref<QualityData | null>(null);
const qualitySource = ref<string>("live");

async function loadStatus() {
  statusLoading.value = true;
  try {
    const [state, metaResult, qualityResult] = await Promise.all([
      fetchTaskCenterStatus(),
      fetchWealthMeta().catch(() => null),
      fetchV3Quality(undefined, 1, 1).catch(() => null),
    ]);
    status.value = state;
    statusError.value = state.errors.length ? state.errors.join("；") : null;
    if (metaResult) {
      wealthMeta.value = metaResult.envelope.data;
      wealthMetaSource.value = metaResult.source;
    } else {
      wealthMeta.value = null;
    }
    if (qualityResult) {
      quality.value = qualityResult.envelope.data;
      qualitySource.value = qualityResult.source;
    } else {
      quality.value = null;
    }
  } finally {
    statusLoading.value = false;
  }
}

onMounted(loadStatus);
usePolling(loadStatus, 5 * 60_000, 10 * 60_000);

const RUN_STATUS_CLS: Record<string, string> = {
  QUEUED: "",
  RUNNING: "action",
  SUCCEEDED: "ok",
  FAILED: "veto",
  CANCELLED: "warn",
};
const RUN_STATUS_LABELS: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "执行中",
  SUCCEEDED: "执行成功",
  FAILED: "执行失败",
  CANCELLED: "已取消",
};
const recentFailedCount = computed(() => runRecords.value.filter((r) => r.status === "FAILED").length);

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 19);
}

/* ---------------- 操作表单 ---------------- */

const paramValues = reactive<Record<string, Record<string, string>>>({});

function paramsOf(op: TaskOpDef): Record<string, string> {
  if (!paramValues[op.id]) {
    const initial: Record<string, string> = {};
    for (const spec of op.params) initial[spec.key] = spec.default === undefined ? "" : String(spec.default);
    paramValues[op.id] = initial;
  }
  return paramValues[op.id]!;
}

const runningOpId = ref<string | null>(null);
const cardError = ref<Record<string, string>>({});
const pendingConfirm = ref<TaskOpDef | null>(null);
const confirmRequestPreview = ref("");

function opErrorOf(op: TaskOpDef): string {
  return cardError.value[op.id] ?? "";
}

function requestPreviewOf(op: TaskOpDef): string {
  const payload = buildPayload(op, paramsOf(op));
  return JSON.stringify(payload, null, 2);
}

function requestRun(op: TaskOpDef) {
  cardError.value = { ...cardError.value, [op.id]: "" };
  const invalid = validateOp(op, paramsOf(op));
  if (invalid) {
    cardError.value = { ...cardError.value, [op.id]: invalid };
    return;
  }
  if (op.confirm) {
    pendingConfirm.value = op;
    confirmRequestPreview.value = requestPreviewOf(op);
    return;
  }
  void doRun(op);
}

function confirmRun() {
  const op = pendingConfirm.value;
  pendingConfirm.value = null;
  if (op) void doRun(op);
}

async function doRun(op: TaskOpDef) {
  runningOpId.value = op.id;
  try {
    const record = await executeOp(op, paramsOf(op), (live) => {
      // job 模式：轮询期间实时刷新任务记录（真实进度，不模拟）
      const index = runRecords.value.findIndex((r) => r.runKey === live.runKey);
      if (index >= 0) runRecords.value.splice(index, 1, { ...live });
      else runRecords.value = [live, ...runRecords.value].slice(0, 50);
    });
    const index = runRecords.value.findIndex((r) => r.runKey === record.runKey);
    if (index >= 0) runRecords.value.splice(index, 1, { ...record });
    else recordRun(record);
    persistRecords();
    await loadStatus();
  } catch (err) {
    cardError.value = { ...cardError.value, [op.id]: errorMessage(err) };
  } finally {
    runningOpId.value = null;
  }
}

/* ---------------- 任务记录 ---------------- */

const RECORDS_KEY = "wl-task-run-records-v1";

const runRecords = ref<TaskRunRecord[]>(loadRecords());
const expandedRunKey = ref<string | null>(null);

function loadRecords(): TaskRunRecord[] {
  try {
    const raw = localStorage.getItem(RECORDS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as TaskRunRecord[];
    return Array.isArray(parsed) ? parsed.slice(0, 50) : [];
  } catch {
    return [];
  }
}

function recordRun(record: TaskRunRecord) {
  runRecords.value = [record, ...runRecords.value].slice(0, 50);
  persistRecords();
}

function persistRecords() {
  try {
    localStorage.setItem(RECORDS_KEY, JSON.stringify(runRecords.value));
  } catch {
    /* 容量满等场景静默降级为内存记录 */
  }
}

function clearRecords() {
  runRecords.value = [];
  try {
    localStorage.removeItem(RECORDS_KEY);
  } catch {
    /* ignore */
  }
}

function toggleRun(runKey: string) {
  expandedRunKey.value = expandedRunKey.value === runKey ? null : runKey;
}

const dataOps = computed(() => TASK_OPS.filter((op) => op.group === "data"));
const researchOps = computed(() => TASK_OPS.filter((op) => op.group === "research"));

/* ---------------- 后端任务队列（v3.2 jobs：真实列表/进度/取消/日志） ---------------- */

const backendJobs = ref<JobPublic[] | null>(null);
const backendJobsError = ref<string | null>(null);
const backendJobsLoading = ref(false);
let jobsSeq = 0;

async function loadBackendJobs() {
  const seq = ++jobsSeq;
  backendJobsLoading.value = true;
  try {
    const jobs = await fetchJobs();
    if (seq !== jobsSeq) return;
    backendJobs.value = jobs;
    backendJobsError.value = null;
  } catch (err) {
    if (seq === jobsSeq) backendJobsError.value = errorMessage(err);
  } finally {
    if (seq === jobsSeq) backendJobsLoading.value = false;
  }
}

onMounted(loadBackendJobs);
usePolling(loadBackendJobs, 8_000, 15_000);

const activeJobCount = computed(
  () => (backendJobs.value ?? []).filter((j) => j.status === "QUEUED" || j.status === "RUNNING").length,
);

const expandedJobId = ref<string | null>(null);
const jobLogs = ref<Record<string, string[]>>({});

async function toggleJobLogs(jobId: string) {
  if (expandedJobId.value === jobId) {
    expandedJobId.value = null;
    return;
  }
  expandedJobId.value = jobId;
  try {
    const result = await fetchJobLogs(jobId);
    jobLogs.value = { ...jobLogs.value, [jobId]: result.logs };
  } catch (err) {
    jobLogs.value = { ...jobLogs.value, [jobId]: [errorMessage(err)] };
  }
}

const cancellingJobId = ref<string | null>(null);

async function doCancelJob(job: JobPublic) {
  cancellingJobId.value = job.job_id;
  try {
    await cancelJob(job.job_id, "前端任务中心发起取消");
    await loadBackendJobs();
  } catch (err) {
    backendJobsError.value = errorMessage(err);
  } finally {
    cancellingJobId.value = null;
  }
}

const JOB_STATUS_CLS: Record<string, string> = {
  QUEUED: "badge",
  RUNNING: "badge action",
  SUCCEEDED: "badge ok",
  FAILED: "badge veto",
  CANCELLED: "badge warn",
};
const JOB_STATUS_LABELS: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "运行中",
  SUCCEEDED: "成功",
  FAILED: "失败",
  CANCELLED: "已取消",
};

function jobLabel(operation: string): string {
  return JOB_OPERATION_LABELS[operation] ?? operation;
}

const providerChips = computed(() => {
  const items = status.value?.providers;
  if (!items || !items.length) return [];
  return items.map((item) => ({
    name: String(item.provider ?? item.name ?? "provider"),
    status: String(item.status ?? (item.healthy === false ? "DOWN" : "UNKNOWN")),
    healthy: item.healthy !== false && String(item.status ?? "").toUpperCase() !== "DOWN",
  }));
});
</script>

<template>
  <div class="task-center">
    <p class="page-sub">
      所有操作按钮均调用真实后端接口：数据与研究任务提交到后端受控任务队列（受理后返回任务编号，进度为真实轮询），
      Router 门面操作按其当前只读部署如实返回。后端未提供的能力显示「暂不可用」，不伪造成功。
    </p>

    <!-- A. 数据状态面板 -->
    <section class="panel">
      <h3>
        数据状态
        <span class="h3-extra">最后检查 {{ fmtTime(status?.fetchedAt) }}{{ statusLoading ? " · 刷新中…" : "" }}</span>
      </h3>
      <div class="stat-grid">
        <div class="stat-card">
          <span class="stat-label">最近行情日期</span>
          <span class="stat-value">{{ wealthMeta?.pool?.last_date ?? "—" }}</span>
          <span class="stat-foot">wealth 数据池{{ wealthMetaSource === "offline-mock" ? "（Mock）" : "" }}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">研究资产数</span>
          <span class="stat-value">{{ quality?.total ?? "—" }}</span>
          <span class="stat-foot">A 股名录 {{ status?.universe?.total ?? "—" }} 条 · 已按版本登记</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">数据质量</span>
          <span class="stat-value">{{ quality ? `${quality.quality_counts?.QUALIFIED ?? 0}✓` : "—" }}</span>
          <span class="stat-foot">
            降级 {{ quality?.quality_counts?.DEGRADED ?? "—" }} · 拒绝 {{ quality?.quality_counts?.REJECTED ?? "—" }}
            {{ qualitySource === "offline-mock" ? "（Mock）" : "" }}
          </span>
        </div>
        <div class="stat-card">
          <span class="stat-label">全市场快照</span>
          <span class="stat-value">{{ status?.snapshot ? (status.snapshot.stale ? "陈旧" : "可用") : "—" }}</span>
          <span class="stat-foot">截止 {{ fmtTime(status?.snapshot?.data_cutoff) }}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">最后更新时刻</span>
          <span class="stat-value">{{ fmtTime(status?.fetchedAt).slice(11) || "—" }}</span>
          <span class="stat-foot">路由网关：{{ status?.routerHealth?.status === "ok" ? "正常" : (status?.routerHealth?.status ?? "不可达") }}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">待处理任务</span>
          <span class="stat-value">{{ backendJobs === null ? "—" : activeJobCount }}</span>
          <span class="stat-foot">后端串行队列{{ backendJobsLoading ? " · 刷新中" : "" }} · 近期失败 {{ recentFailedCount }}</span>
        </div>
      </div>
      <div v-if="providerChips.length" class="provider-row">
        <span
          v-for="chip in providerChips"
          :key="chip.name"
          class="badge"
          :class="chip.healthy ? 'ok' : 'veto'"
        >{{ chip.name }}：{{ chip.status }}</span>
      </div>
      <div v-if="statusError" class="status-errors" role="alert">{{ statusError }}</div>
    </section>

    <!-- B/C. 数据操作 / 研究操作 -->
    <section v-for="(ops, groupIndex) in [dataOps, researchOps]" :key="groupIndex" class="panel">
      <h3>{{ groupIndex === 0 ? "数据操作（受控写接口）" : "研究操作" }}</h3>
      <div class="op-grid">
        <article v-for="op in ops" :key="op.id" class="op-card" :class="{ unavailable: op.unavailable }">
          <header class="op-head">
            <strong>{{ op.label }}</strong>
            <span v-if="op.unavailable" class="badge warn">暂不可用</span>
            <span
              v-else-if="op.kind === 'job'"
              class="badge"
              :title="`受控任务（后端白名单校验）：${op.operation}`"
            >受控任务 · 真实执行</span>
            <span
              v-else
              class="badge"
              :title="`技术信息（保留审计）：${op.method} ${op.path}`"
            >{{ op.base === "wealth" ? "即时接口 · 财富后端" : "即时接口 · 只读网关" }}</span>
          </header>
          <p class="op-desc">{{ op.description }}</p>
          <p v-if="op.unavailable && op.unavailableReason" class="op-unavailable-reason">{{ op.unavailableReason }}</p>
          <template v-if="!op.unavailable">
            <div class="op-form">
              <label v-for="spec in op.params" :key="spec.key" class="op-field" :title="spec.hint">
                <span>{{ spec.label }}</span>
                <select v-if="spec.kind === 'select'" v-model="paramsOf(op)[spec.key]">
                  <option v-for="option in spec.options" :key="option" :value="option">{{ option }}</option>
                </select>
                <input
                  v-else
                  v-model="paramsOf(op)[spec.key]"
                  :type="spec.kind === 'number' ? 'number' : 'text'"
                  :placeholder="spec.hint || ''"
                />
              </label>
            </div>
            <p v-if="opErrorOf(op)" class="op-error" role="alert">{{ opErrorOf(op) }}</p>
            <footer class="op-foot">
              <button
                class="mini primary"
                type="button"
                :disabled="runningOpId !== null"
                @click="requestRun(op)"
              >{{ runningOpId === op.id ? "执行中…" : "运行" }}</button>
              <details class="op-preview">
                <summary>技术详情（请求参数）</summary>
                <pre>{{ requestPreviewOf(op) }}</pre>
              </details>
            </footer>
          </template>
        </article>
      </div>
    </section>

    <!-- D1. 后端任务队列（v3.2 jobs，真实轮询） -->
    <section class="panel">
      <h3>
        后端任务队列（jobs）
        <span class="h3-extra">
          白名单 8 操作 · DuckDB 单写者串行执行 · 排队 {{ activeJobCount }}
          {{ backendJobsLoading ? " · 刷新中…" : "" }}
        </span>
        <button class="mini" type="button" @click="loadBackendJobs">刷新</button>
      </h3>
      <div v-if="backendJobsError" class="status-errors" role="alert">{{ backendJobsError }}</div>
      <div v-if="backendJobs !== null && !backendJobs.length && !backendJobsError" class="empty">
        队列为空。上方任务按钮提交后将在此显示 job_id、真实进度与结果。
      </div>
      <div v-else-if="backendJobs !== null" class="table-scroll">
        <table class="data">
          <thead>
            <tr>
              <th>job_id</th><th>操作</th><th>状态</th><th class="num">进度</th><th class="num">已写入</th>
              <th>创建 / 完成时间</th><th>结果 / 错误</th><th></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="job in backendJobs" :key="job.job_id">
              <tr>
                <td><code class="job-id">{{ job.job_id }}</code></td>
                <td>{{ jobLabel(job.operation) }}</td>
                <td><span :class="JOB_STATUS_CLS[job.status] ?? 'badge'">{{ JOB_STATUS_LABELS[job.status] ?? job.status }}</span></td>
                <td class="num">
                  <span class="progress-cell"><span class="progress-bar"><span :style="{ width: `${job.progress}%` }"></span></span>{{ job.progress }}%</span>
                </td>
                <td class="num">{{ job.records_written || "—" }}</td>
                <td class="num">{{ fmtTime(job.created_at) }}<br /><small class="run-path">{{ job.finished_at ? fmtTime(job.finished_at) : "进行中" }}</small></td>
                <td class="job-result-cell">
                  <template v-if="job.error"><span class="error-text">{{ job.error }}</span></template>
                  <template v-else-if="job.result && Object.keys(job.result).length">{{ JSON.stringify(job.result) }}</template>
                  <template v-else>—</template>
                </td>
                <td class="job-actions">
                  <button class="mini" type="button" @click="toggleJobLogs(job.job_id)">{{ expandedJobId === job.job_id ? "收起日志" : "日志" }}</button>
                  <button
                    v-if="job.status === 'QUEUED' || job.status === 'RUNNING'"
                    class="mini danger"
                    type="button"
                    :disabled="cancellingJobId !== null"
                    @click="doCancelJob(job)"
                  >{{ cancellingJobId === job.job_id ? "取消中…" : "取消" }}</button>
                </td>
              </tr>
              <tr v-if="expandedJobId === job.job_id">
                <td colspan="8" class="logs-cell">
                  <h4>执行日志（后端真实记录，最近 100 条）</h4>
                  <pre>{{ (jobLogs[job.job_id] ?? ["加载中…"]).join("\n") }}</pre>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <div v-else class="loading">正在读取后端任务队列…</div>
    </section>

    <!-- D2. 本地留痕（含同步接口与已完结 job） -->
    <section class="panel">
      <h3>
        任务记录（本地留痕）
        <span class="h3-extra">job 任务轮询期间实时更新 · 保留最近 50 条（本地）</span>
      </h3>
      <div v-if="!runRecords.length" class="empty">暂无任务记录。上方每个「运行」按钮的真实调用（含失败）都会留痕于此。</div>
      <template v-else>
        <div class="view-actions">
          <span>共 {{ runRecords.length }} 条 · 失败 {{ recentFailedCount }} 条</span>
          <button class="mini" type="button" @click="clearRecords">清空记录</button>
        </div>
        <div class="table-scroll">
          <table class="data">
            <thead>
              <tr>
                <th>操作</th>
                <th>状态</th>
                <th>开始时间</th>
                <th>耗时</th>
                <th>引用编号（审计）</th>
                <th>结果摘要 / 失败原因</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <template v-for="record in runRecords" :key="record.runKey">
                <tr>
                  <td>{{ record.opLabel }}<small class="run-path">{{ record.path.includes("/jobs") ? "经受控任务队列执行" : "经只读网关执行（当前部署）" }}</small></td>
                  <td><span class="badge" :class="RUN_STATUS_CLS[record.status] ?? (record.status === 'SUCCEEDED' ? 'ok' : 'veto')">{{ RUN_STATUS_LABELS[record.status] ?? record.status }}</span></td>
                  <td class="num">{{ fmtTime(record.startedAt) }}</td>
                  <td class="num">{{ (record.durationMs / 1000).toFixed(1) }}s</td>
                  <td class="num">{{ record.backendRefId ?? record.jobId ?? "—" }}</td>
                  <td :class="record.status === 'FAILED' ? 'error-text' : ''">{{ record.error ?? record.resultSummary }}</td>
                  <td><button class="mini" type="button" @click="toggleRun(record.runKey)">{{ expandedRunKey === record.runKey ? "收起" : "详情" }}</button></td>
                </tr>
                <tr v-if="expandedRunKey === record.runKey" class="detail-row">
                  <td colspan="7">
                    <div class="detail-grid">
                      <div>
                        <h4>请求参数（技术审计）</h4>
                        <pre>{{ JSON.stringify(record.request, null, 2) }}</pre>
                      </div>
                      <div>
                        <h4>后端响应原文（审计留痕）</h4>
                        <pre>{{ JSON.stringify(record.resultPayload, null, 2) ?? "—" }}</pre>
                      </div>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </template>
    </section>

    <!-- 确认对话框（危险/写库操作） -->
    <div v-if="pendingConfirm" class="modal-mask" @click.self="pendingConfirm = null">
      <div class="modal confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
        <div class="modal-head">
          <h3 id="confirm-title">确认执行：{{ pendingConfirm.label }}</h3>
          <button class="close-btn" type="button" aria-label="取消" @click="pendingConfirm = null">✕</button>
        </div>
        <p class="confirm-text">{{ pendingConfirm.confirm }}</p>
        <h4>将发送的请求体（POST {{ pendingConfirm.path }}）</h4>
        <pre class="confirm-request">{{ confirmRequestPreview }}</pre>
        <div class="confirm-actions">
          <button class="mini" type="button" @click="pendingConfirm = null">取消</button>
          <button class="mini danger" type="button" @click="confirmRun">确认执行</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.provider-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.status-errors { margin-top: 10px; color: var(--veto); font-size: 12px; }
.job-id { font-size: 11.5px; color: var(--accent-strong); font-variant-numeric: tabular-nums; }
.progress-cell { display: inline-flex; align-items: center; gap: 7px; }
.progress-bar { display: inline-block; width: 76px; height: 6px; border-radius: 4px; background: var(--bg-hover); overflow: hidden; }
.progress-bar > span { display: block; height: 100%; border-radius: 4px; background: var(--accent-grad); transition: width 0.5s ease; }
.job-result-cell { max-width: 300px; white-space: normal; word-break: break-all; font-size: 11.5px; color: var(--text-dim); }
.job-actions { display: flex; gap: 6px; }
.logs-cell { background: var(--bg-hover); white-space: normal; }
.logs-cell h4 { margin: 0 0 6px; font-size: 12px; color: var(--text-dim); }
.logs-cell pre { margin: 0; font-size: 11px; max-height: 220px; overflow: auto; background: var(--control-bg); border: 1px solid var(--border-soft); border-radius: 8px; padding: 8px; white-space: pre-wrap; word-break: break-all; }
.op-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.op-card { border: 1px solid var(--border); border-radius: var(--radius-inner); padding: 12px 14px; background: var(--bg-panel-2); display: flex; flex-direction: column; gap: 8px; }
.op-card.unavailable { opacity: 0.75; }
.op-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.op-head strong { font-size: 13.5px; }
.op-desc { margin: 0; font-size: 12px; color: var(--text-dim); line-height: 1.55; }
.op-unavailable-reason { margin: 0; font-size: 12px; color: var(--warn); }
.op-form { display: flex; flex-wrap: wrap; gap: 8px; }
.op-field { display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; color: var(--text-dim); }
.op-field input, .op-field select { min-width: 120px; font-size: 12.5px; padding: 5px 9px; }
.op-error { margin: 0; color: var(--veto); font-size: 12px; }
.op-foot { display: flex; align-items: center; gap: 10px; margin-top: auto; }
.op-preview { margin-left: auto; font-size: 11.5px; color: var(--text-dim); }
.op-preview summary { cursor: pointer; }
.op-preview pre { max-width: 320px; max-height: 180px; overflow: auto; font-size: 11px; background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 8px; padding: 8px; }
.run-path { display: block; color: var(--text-faint); font-size: 10.5px; }
.error-text { color: var(--veto); }
.detail-row td { background: var(--bg-hover); white-space: normal; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.detail-grid h4 { margin: 0 0 6px; font-size: 12px; color: var(--text-dim); }
.detail-grid pre, .confirm-request { margin: 0; font-size: 11px; max-height: 240px; overflow: auto; background: var(--control-bg); border: 1px solid var(--border-soft); border-radius: 8px; padding: 8px; white-space: pre-wrap; word-break: break-all; }
.confirm-modal { width: min(680px, 94vw); }
.confirm-text { color: var(--text); font-size: 13px; }
.confirm-modal h4 { margin: 10px 0 6px; font-size: 12.5px; color: var(--text-dim); }
.confirm-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
@media (max-width: 900px) {
  .detail-grid { grid-template-columns: 1fr; }
}
</style>
