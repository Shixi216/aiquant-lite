<script setup lang="ts">
import { onMounted, ref } from "vue";
import { fetchDashboard } from "../api/v2";
import { usePolling } from "../composables/usePolling";
import type { DashboardResponse } from "../types/v2";
import DataFreshnessBar from "../components/global/DataFreshnessBar.vue";
import HealthChip from "../components/global/HealthChip.vue";
import ProvenanceLine from "../components/global/ProvenanceLine.vue";
import SummaryCards from "../components/dashboard/SummaryCards.vue";
import BreadthChart from "../components/dashboard/BreadthChart.vue";
import RiskCenter from "../components/dashboard/RiskCenter.vue";
import MarketContext from "../components/dashboard/MarketContext.vue";
import DragonTigerBoard from "../components/dashboard/DragonTigerBoard.vue";
import { errorMessage } from "../api/client";

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
</script>

<template>
  <div class="dashboard-view">
    <div class="view-actions"><span>市场状态与数据新鲜度</span><button class="mini" :disabled="loading" @click="load">{{ loading ? '更新中…' : '刷新数据' }}</button></div>
    <div v-if="dashboard?.fixture_mode" class="fixture-banner" role="status">演示样本 · 当前内容仅用于界面验证</div>
    <div v-if="loading && !dashboard" class="loading" role="status">正在读取市场总览…</div>
    <div v-if="error" class="error-banner">数据服务不可达：{{ error }}</div>
    <template v-if="dashboard">
      <DataFreshnessBar :time-semantics="dashboard.time_semantics" />
      <section class="panel">
        <h3>数据组件状态</h3>
        <div class="chips">
          <HealthChip
            v-for="chip in dashboard.components"
            :key="chip.name"
            :name="chip.name"
            :status="chip.status"
            :healthy="chip.healthy"
          />
        </div>
      </section>
      <SummaryCards :breadth="dashboard.market_breadth" />
      <BreadthChart :breadth="dashboard.market_breadth.data" />
      <MarketContext :context="dashboard.market_context" />
      <DragonTigerBoard :board="dashboard.dragon_tiger" />
      <RiskCenter
        :coverage="dashboard.coverage"
        :breadth="dashboard.market_breadth"
        :components="dashboard.components"
      />
      <ProvenanceLine
        :source="dashboard.provenance.data_source"
        :formal-status="dashboard.provenance.formal_status"
      />
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
</style>
