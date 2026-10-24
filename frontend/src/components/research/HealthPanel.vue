<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api, errorMessage } from "../../api/client";
import { getComponentLabel, getHealthStatusLabel, formatDateTime } from "../../domain/labels";

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
  providers?: { name?: string; provider?: string; status?: string; state?: string }[];
}

const health = ref<SystemHealth | null>(null);
const providers = ref<ProviderHealth | null>(null);
const coverage = ref<{ data_cutoff?: string; universe_version?: string; metrics?: Record<string, CoverageMetric> } | null>(null);
const error = ref<string | null>(null);
const loading = ref(false);

const statusClass = (status: string | undefined) => {
  const value = status?.toUpperCase();
  if (["HEALTHY", "OK", "CLOSED", "READY"].includes(value ?? "")) return "ok";
  if (["DEGRADED", "NOT_READY", "STALE", "HALF_OPEN"].includes(value ?? "")) return "warn-badge";
  return ["ERROR", "FAILED", "OPEN", "UNREACHABLE"].includes(value ?? "") ? "veto" : "";
};
const descriptions: Record<string, string> = {
  router: "本地数据与研究服务的响应状态", database: "主数据库只读查询检查",
  migrations: "已部署数据库结构与校验和检查", data_hub: "数据中心服务的就绪状态",
  finance_mcp: "本地金融工具注册状态", scanner: "选股扫描的数据与服务准备情况",
  formal_evaluation: "正式评估所需数据的准备情况", fundamental_coverage: "基本面数据覆盖情况",
  sentiment_coverage: "情绪数据覆盖情况", policy_news_coverage: "政策与新闻覆盖情况",
  capital_flow_coverage: "资金流数据覆盖情况", wecom: "可选企业微信通道的配置状态",
  live_trading: "公开实现没有自动实盘执行入口",
};
const coverageLabels: Record<string, string> = {
  active_listing: "正常上市证券", announcements_30d: "近30日公告", capital_flow: "资金流",
  event_linking: "事件关联", finance_news_30d_legacy: "近30日财经新闻（历史口径）",
  finance_news_provider_window: "财经新闻源窗口", full_text: "公告全文", history_20d: "20日历史行情",
  history_20d_legacy: "20日历史行情（历史口径）", history_60d: "60日历史行情",
  history_60d_legacy: "60日历史行情（历史口径）", industry: "行业分类", model_analyzed_events: "模型分析事件",
  policy_news: "政策新闻", realtime_snapshot: "市场行情快照", sentiment: "情绪数据", turnover: "换手数据",
};

async function refresh(silent = false) {
  if (!silent) loading.value = true;
  error.value = null;
  const [h, p, c] = await Promise.allSettled([
      api.get("/v1/integration/system/health"),
      api.get("/v1/providers/health"),
      api.get("/v1/data-coverage/latest"),
  ]);
  health.value = h.status === "fulfilled" ? h.value.data : null;
  providers.value = p.status === "fulfilled" ? p.value.data : null;
  coverage.value = c.status === "fulfilled" ? c.value.data : null;
  const results = [h, p, c];
  const labels = ["系统组件", "数据源状态", "数据覆盖"];
  error.value = results.flatMap((result, index) => result.status === "rejected"
    ? [`${labels[index]}：${errorMessage(result.reason)}`] : []).join("；") || null;
  loading.value = false;
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
    <span v-if="coverage?.data_cutoff" class="badge">覆盖报告截至 {{ formatDateTime(coverage.data_cutoff) }}（北京时间）</span>
  </div>
  <div v-if="error" class="error-box">{{ error }}</div>
  <div class="health-sections">
    <div class="panel">
      <h3>系统组件</h3>
      <div class="component-grid">
        <div v-for="component in health?.data?.components ?? []" :key="component.name" class="component-card">
          <div class="component-head">
            <strong>{{ getComponentLabel(component.name) }}</strong>
            <span class="badge" :class="statusClass(component.status)">{{ getHealthStatusLabel(component.status) }}</span>
            <span v-if="!component.required" class="badge">可选组件</span>
          </div>
          <div class="component-detail">{{ descriptions[component.name] ?? '组件就绪与数据可用性检查' }}</div>
          <details v-if="component.detail"><summary>诊断原文</summary><p class="component-detail">{{ component.detail }}</p></details>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3>数据源熔断状态</h3>
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
              <td>{{ getComponentLabel(provider.name ?? provider.provider ?? `数据源${index + 1}`) }}</td>
              <td><span class="badge" :class="statusClass(provider.status ?? provider.state)">{{ getHealthStatusLabel(provider.status ?? provider.state) }}</span></td>
            </tr>
          </tbody>
        </table>
      </template>
      <div v-else class="empty">当前没有可用的数据源注册状态。</div>
    </div>

    <div class="panel">
      <h3>数据覆盖度 {{ coverage?.universe_version ? `· ${coverage.universe_version}` : "" }}</h3>
      <table v-if="coverage?.metrics" class="data">
        <thead>
          <tr><th>指标</th><th class="num">覆盖</th><th class="num">样本</th><th>比例</th></tr>
        </thead>
        <tbody>
          <tr v-for="(metric, name) in coverage.metrics" :key="name">
            <td :title="String(name)">{{ coverageLabels[name] ?? '其他覆盖指标' }}</td>
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
  </div>
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
