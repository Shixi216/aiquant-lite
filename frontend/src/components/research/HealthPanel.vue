<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api, errorMessage } from "../../api/client";
import { getComponentLabel, getHealthStatusLabel } from "../../domain/labels";

interface CoverageMetric {
  numerator: number;
  denominator: number;
  ratio: number;
}

interface SystemHealth {
  data?: {
    overall_status?: string;
    components?: { name: string; status: string; detail: string; required: boolean }[];
  };
}

interface ProviderHealth {
  provider_count?: number;
  open_breakers?: string[];
  providers?: { name?: string; status?: string }[];
}

const health = ref<SystemHealth | null>(null);
const providers = ref<ProviderHealth | null>(null);
const coverage = ref<{ data_cutoff?: string; universe_version?: string; metrics?: Record<string, CoverageMetric> } | null>(null);
const error = ref<string | null>(null);
const loading = ref(false);

const statusClass = (status: string | undefined) =>
  status === "HEALTHY" || status === "ok"
    ? "ok"
    : status === "DEGRADED"
      ? "warn-badge"
      : status
        ? "veto"
        : "";

async function refresh(silent = false) {
  if (!silent) loading.value = true;
  error.value = null;
  try {
    const [h, p, c] = await Promise.all([
      api.get("/v1/integration/system/health"),
      api.get("/v1/providers/health"),
      api.get("/v1/data-coverage/latest"),
    ]);
    health.value = h.data;
    providers.value = p.data;
    coverage.value = c.data;
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

onMounted(() => refresh());
defineExpose({ refresh });
</script>

<template>
  <div class="toolbar">
    <button class="mini" :disabled="loading" @click="refresh()">刷新</button>
    <span v-if="health?.data?.overall_status" class="badge" :class="statusClass(health.data.overall_status)">
      {{ getHealthStatusLabel(health.data.overall_status) }}
    </span>
    <span v-if="coverage?.data_cutoff" class="badge">数据截止 {{ coverage.data_cutoff.replace("T", " ").slice(0, 16) }}</span>
  </div>
  <div v-if="error" class="error-box">{{ error }}</div>
  <template v-else>
    <div class="panel">
      <h3>系统组件</h3>
      <div class="component-grid">
        <div v-for="component in health?.data?.components ?? []" :key="component.name" class="component-card">
          <div class="component-head">
            <strong>{{ getComponentLabel(component.name) }}</strong>
            <span class="badge" :class="statusClass(component.status)">{{ getHealthStatusLabel(component.status) }}</span>
            <span v-if="!component.required" class="badge">可选组件</span>
          </div>
          <div class="component-detail">{{ component.detail }}</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3>数据源熔断（providers）</h3>
      <template v-if="providers?.provider_count">
        <div class="toolbar">
          <span class="badge">注册 {{ providers.provider_count }}</span>
          <span class="badge" :class="(providers.open_breakers?.length ?? 0) > 0 ? 'veto' : 'ok'">
            熔断中 {{ providers.open_breakers?.length ?? 0 }}
          </span>
        </div>
        <table class="data">
          <thead>
            <tr><th>数据源</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="(provider, index) in providers.providers ?? []" :key="index">
              <td>{{ provider.name ?? index }}</td>
              <td><span class="badge" :class="statusClass(provider.status)">{{ getHealthStatusLabel(provider.status) }}</span></td>
            </tr>
          </tbody>
        </table>
      </template>
      <div v-else class="empty">当前无注册的数据源 provider。</div>
    </div>

    <div class="panel">
      <h3>数据覆盖度 {{ coverage?.universe_version ? `· ${coverage.universe_version}` : "" }}</h3>
      <table v-if="coverage?.metrics" class="data">
        <thead>
          <tr><th>指标</th><th class="num">覆盖</th><th class="num">样本</th><th>比例</th></tr>
        </thead>
        <tbody>
          <tr v-for="(metric, name) in coverage.metrics" :key="name">
            <td>{{ name }}</td>
            <td class="num">{{ metric.numerator }}</td>
            <td class="num">{{ metric.denominator }}</td>
            <td style="min-width: 160px">
              <div class="ratio-bar"><span :style="{ width: `${Math.min(metric.ratio * 100, 100)}%` }" /></div>
              {{ (metric.ratio * 100).toFixed(2) }}%
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">暂无覆盖度报告。</div>
    </div>
  </template>
</template>

<style scoped>
.component-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 10px;
}

.component-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  display: grid;
  gap: 6px;
}

.component-head {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.component-detail {
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.5;
}

.badge.warn-badge {
  color: var(--warn);
  border-color: var(--warn);
}

.ratio-bar {
  display: inline-block;
  vertical-align: middle;
  width: 110px;
  height: 8px;
  background: var(--border);
  border-radius: 4px;
  margin-right: 8px;
  overflow: hidden;
}

.ratio-bar span {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: 4px;
}
</style>
