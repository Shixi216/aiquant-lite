<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { fetchWealthMeta } from "../api/wealthLab";
import { fetchV3Quality } from "../api/wealthLabV3";
import {
  buildPayload,
  executeOp,
  fetchTaskCenterStatus,
  TASK_OPS,
  validateOp,
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
    const record = await executeOp(op, paramsOf(op));
    recordRun(record);
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
      所有操作按钮均调用真实后端接口（Router 8767 / 财富后端 8765）。后端同步接口请求返回即终态；
      异步 job 轮询待后端契约交付后接入。后端未提供的能力显示「暂不可用」，不伪造成功。
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
          <span class="stat-foot">A股名录 {{ status?.universe?.total ?? "—" }} 条 · 版本 {{ status?.universe?.universe_version ?? "—" }}</span>
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
          <span class="stat-foot">Router：{{ status?.routerHealth?.status ?? "不可达" }}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">待处理任务</span>
          <span class="stat-value">0</span>
          <span class="stat-foot">同步接口无排队 · 近期失败 {{ recentFailedCount }}</span>
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
            <span v-else class="badge">{{ op.base === "wealth" ? "8765 可写" : "8767 只读门面" }} · {{ op.method }} {{ op.path }}</span>
          </header>
          <p class="op-desc">{{ op.description }}</p>
          <p v-if="op.unavailable && op.unavailableReason" class="op-unavailable-reason">{{ op.unavailableReason }}</p>
          <template v-if="!op.unavailable && op.params.length">
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
                <summary>请求体预览</summary>
                <pre>{{ requestPreviewOf(op) }}</pre>
              </details>
            </footer>
          </template>
        </article>
      </div>
    </section>

    <!-- D. 任务管理 -->
    <section class="panel">
      <h3>
        任务记录
        <span class="h3-extra">同步接口：请求返回即终态 · 保留最近 50 条（本地）</span>
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
                <th>后端引用 ID</th>
                <th>结果摘要 / 失败原因</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <template v-for="record in runRecords" :key="record.runKey">
                <tr>
                  <td>{{ record.opLabel }}<small class="run-path">{{ record.path }}</small></td>
                  <td><span class="badge" :class="record.status === 'SUCCEEDED' ? 'ok' : 'veto'">{{ record.status }}</span></td>
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
                        <h4>请求体</h4>
                        <pre>{{ JSON.stringify(record.request, null, 2) }}</pre>
                      </div>
                      <div>
                        <h4>响应（审计留痕）</h4>
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
