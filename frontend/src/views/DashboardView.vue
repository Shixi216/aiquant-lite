<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { fetchDashboard } from "../api/v2";
import { usePolling } from "../composables/usePolling";
import type { DashboardResponse } from "../types/v2";
import { useThemeStore } from "../stores/theme";
import DataFreshnessBar from "../components/global/DataFreshnessBar.vue";
import HealthChip from "../components/global/HealthChip.vue";
import ProvenanceLine from "../components/global/ProvenanceLine.vue";
import SummaryCards from "../components/dashboard/SummaryCards.vue";
import BreadthChart from "../components/dashboard/BreadthChart.vue";
import RiskCenter from "../components/dashboard/RiskCenter.vue";
import MarketContext from "../components/dashboard/MarketContext.vue";
import DragonTigerBoard from "../components/dashboard/DragonTigerBoard.vue";
import { errorMessage } from "../api/client";
import PixelIcon from "../components/global/PixelIcon.vue";

const themeStore = useThemeStore();
const dashboard = ref<DashboardResponse | null>(null);
const error = ref<string | null>(null);

const loading = ref(false);
let latestRequest = 0;

async function load() {
  const request = ++latestRequest;
  loading.value = true;
  try {
    const result = await fetchDashboard();
    if (request !== latestRequest) return;
    dashboard.value = result;
    error.value = null;
  } catch (err) {
    if (request === latestRequest) error.value = errorMessage(err);
  } finally {
    if (request === latestRequest) loading.value = false;
  }
}

onMounted(load);
usePolling(load, 5 * 60 * 1000, 5 * 60 * 1000);

/** 按「外观与布局」设置渲染：顺序 + 显隐可定制（市场总览组件）。 */
/** 组件状态分组：有明确判定结果的才逐个展示，unknown 聚合为一枚徽章（悬浮可查看清单）。 */
const activeComponents = computed(() =>
  (dashboard.value?.components ?? []).filter(
    (chip) => chip.status !== "unknown" && String(chip.status ?? "").trim() !== "",
  ),
);

function widgetHasDisplayData(id: string): boolean {
  const data = dashboard.value;
  if (!data) return false;
  if (id === "freshness") return Object.values(data.time_semantics).some((field) => Boolean(field.value));
  if (id === "components") return activeComponents.value.length > 0;
  if (id === "summary" || id === "breadth") return data.market_breadth.available && Boolean(data.market_breadth.data);
  if (id === "context") return data.market_context.available && Boolean(data.market_context.data);
  if (id === "dragon-tiger") return data.dragon_tiger.available && Boolean(data.dragon_tiger.data?.items.length);
  return true;
}

const visibleWidgets = computed(() =>
  themeStore.settings.dashboardOrder.filter(
    (id) => !themeStore.settings.dashboardHidden.includes(id) && widgetHasDisplayData(id),
  ),
);

const widgetNames: Record<string, string> = {
  freshness: "数据新鲜度条",
  components: "数据组件状态",
  summary: "核心指标卡",
  breadth: "涨跌家数图表",
  context: "市场环境",
  "dragon-tiger": "龙虎榜",
  risk: "风险中心",
  provenance: "数据溯源",
};

function moveWidget(id: string, direction: -1 | 1) {
  themeStore.reorderDashboard(id, themeStore.settings.dashboardOrder[themeStore.settings.dashboardOrder.indexOf(id) + direction] ?? id);
}
</script>

<template>
  <div class="dashboard-view">
    <div class="view-actions">
      <span>市场状态与数据新鲜度</span>
      <span class="dash-tools">
        <details class="widget-manager">
          <summary class="mini">组件显示与排列</summary>
          <ul class="widget-manager-list">
            <li v-for="(id, index) in themeStore.settings.dashboardOrder" :key="id">
              <label>
                <input
                  type="checkbox"
                  :checked="!themeStore.settings.dashboardHidden.includes(id)"
                  @change="themeStore.toggleDashboardWidget(id)"
                />
                {{ widgetNames[id] ?? id }}
              </label>
              <span class="widget-order">
                <button class="mini" type="button" :disabled="index === 0" @click="moveWidget(id, -1)"><PixelIcon name="arrow-up" :size="14" /></button>
                <button class="mini" type="button" :disabled="index === themeStore.settings.dashboardOrder.length - 1" @click="moveWidget(id, 1)"><PixelIcon name="arrow-down" :size="14" /></button>
              </span>
            </li>
          </ul>
        </details>
        <button class="mini" :disabled="loading" @click="load">{{ loading ? '更新中…' : '刷新数据' }}</button>
      </span>
    </div>
    <div v-if="dashboard?.fixture_mode" class="fixture-banner" role="status">演示样本 · 当前内容仅用于界面验证</div>
    <div v-if="loading && !dashboard" class="loading" role="status">正在读取市场总览…</div>
    <div v-if="error" class="error-banner">数据服务不可达：{{ error }}</div>
    <template v-if="dashboard">
      <template v-for="widget in visibleWidgets" :key="widget">
        <DataFreshnessBar v-if="widget === 'freshness'" :time-semantics="dashboard.time_semantics" />
        <section v-else-if="widget === 'components'" class="panel">
          <h3>
            数据组件状态
            <span class="h3-extra">仅展示已验证状态</span>
          </h3>
          <div class="chips">
            <HealthChip
              v-for="chip in activeComponents"
              :key="chip.name"
              :name="chip.name"
              :status="chip.status"
              :healthy="chip.healthy"
            />
          </div>
        </section>
        <SummaryCards v-else-if="widget === 'summary'" :breadth="dashboard.market_breadth" />
        <BreadthChart v-else-if="widget === 'breadth'" :breadth="dashboard.market_breadth.data" />
        <MarketContext v-else-if="widget === 'context'" :context="dashboard.market_context" />
        <DragonTigerBoard v-else-if="widget === 'dragon-tiger'" :board="dashboard.dragon_tiger" />
        <RiskCenter
          v-else-if="widget === 'risk'"
          :coverage="dashboard.coverage"
          :breadth="dashboard.market_breadth"
          :components="dashboard.components"
        />
        <ProvenanceLine
          v-else-if="widget === 'provenance'"
          :source="dashboard.provenance.data_source"
          :formal-status="dashboard.provenance.formal_status"
        />
      </template>
    </template>
  </div>
</template>

<style scoped>
.error-banner {
  background: var(--bg-panel);
  border: 1px solid var(--veto);
  color: var(--veto);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 16px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.dash-tools { display: inline-flex; align-items: center; gap: 8px; }
.widget-manager { position: relative; }
.widget-manager summary { list-style: none; cursor: pointer; }
.widget-manager summary::-webkit-details-marker { display: none; }
.widget-manager-list { position: absolute; right: 0; top: calc(100% + 6px); z-index: 30; width: 240px; margin: 0; padding: 10px; list-style: none; background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius-inner); box-shadow: var(--shadow-pop); display: flex; flex-direction: column; gap: 6px; }
.widget-manager-list li { display: flex; justify-content: space-between; align-items: center; font-size: 12.5px; }
.widget-manager-list label { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.widget-manager-list input { accent-color: var(--accent); }
.widget-order { display: inline-flex; gap: 4px; }
.widget-order .mini { padding: 2px 8px; }
</style>
