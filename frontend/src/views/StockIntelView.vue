<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { fetchChart, fetchStockIntel } from "../api/v2";
import { errorMessage } from "../api/client";
import { usePolling } from "../composables/usePolling";
import type { ChartResponse, StockIntelResponse } from "../types/v2";
import DecisionCard from "../components/stock/DecisionCard.vue";
import FourDimensionBars from "../components/stock/FourDimensionBars.vue";
import KlineChart from "../components/stock/KlineChart.vue";
import RiskPanel from "../components/stock/RiskPanel.vue";
import ProvenanceLine from "../components/global/ProvenanceLine.vue";
import AdjustmentBadge from "../components/global/AdjustmentBadge.vue";
import BlockState from "../components/global/BlockState.vue";
import StockDatabaseContext from "../components/stock/StockDatabaseContext.vue";
import { getBlockReasonLabel, hasInconsistentTurnover } from "../domain/labels";

const route = useRoute();
const symbol = computed(() => String(route.params.symbol ?? "600519.SH"));

const intel = ref<StockIntelResponse | null>(null);
const chart = ref<ChartResponse | null>(null);
const inconsistentTurnover = computed(() => chart.value?.bars.some(hasInconsistentTurnover) ?? false);
const days = ref(60);
const error = ref<string | null>(null);
const chartError = ref<string | null>(null);
const loading = ref(false);
const chartLoading = ref(false);
let intelRequest = 0;
let chartRequest = 0;

async function loadIntel() {
  const request = ++intelRequest;
  loading.value = true;
  try {
    const result = await fetchStockIntel(symbol.value);
    if (request !== intelRequest) return;
    intel.value = result;
    error.value = null;
  } catch (err) {
    if (request === intelRequest) error.value = errorMessage(err);
  } finally {
    if (request === intelRequest) loading.value = false;
  }
}
async function loadChart() {
  const request = ++chartRequest;
  chartLoading.value = true;
  chartError.value = null;
  try {
    const result = await fetchChart(symbol.value, days.value);
    if (request === chartRequest) chart.value = result;
  } catch (err) {
    if (request === chartRequest) {
      chart.value = null;
      chartError.value = errorMessage(err);
    }
  } finally {
    if (request === chartRequest) chartLoading.value = false;
  }
}
async function load() { await Promise.all([loadIntel(), loadChart()]); }

onMounted(load);
watch(symbol, () => {
  intel.value = null;
  chart.value = null;
  error.value = null;
  load();
});
watch(days, () => { chart.value = null; loadChart(); });
usePolling(load, 5 * 60 * 1000, 5 * 60 * 1000);

const decisionReason = computed(() =>
  intel.value && !intel.value.decision_card.available
    ? getBlockReasonLabel(intel.value.decision_card.reason_code)
    : null
);
const crossReason = computed(() =>
  intel.value && !intel.value.four_dimensions.available
    ? getBlockReasonLabel(intel.value.four_dimensions.reason_code)
    : null
);
const zonePacket = computed(() =>
  intel.value?.decision_card.available ? intel.value.decision_card.data : null
);
const showDegradedChartHint = computed(() => {
  const adjustment = chart.value?.adjustment;
  return !!adjustment && adjustment.effective_adjustment_type !== "FORWARD_ADJUSTED";
});
</script>

<template>
  <div class="stock-intel-view">
    <div class="view-actions"><span>查看行情、四维评分与已有决策记录</span><button class="mini" :disabled="loading || chartLoading" @click="load">{{ loading || chartLoading ? '更新中…' : '刷新数据' }}</button></div>
    <div v-if="intel?.fixture_mode || chart?.fixture_mode" class="fixture-banner" role="status">演示样本 · 当前内容仅用于界面验证</div>
    <div v-if="loading && !intel" class="loading" role="status">正在读取个股研究…</div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <template v-if="intel || chart || chartLoading || chartError">
      <h2 class="page-title">{{ intel?.identity.name ? `${intel.identity.name} · ` : '' }}{{ symbol }} <span class="stock-caption">个股研究</span></h2>
      <StockDatabaseContext v-if="intel" :context="intel.database_context" />
      <DecisionCard :packet="zonePacket" :reason="decisionReason" />
      <div class="two-col">
        <FourDimensionBars
          :data="intel?.four_dimensions.available ? intel.four_dimensions.data : null"
          :reason="crossReason"
        />
        <RiskPanel
          :risks="intel?.risk.available ? (intel.risk.data?.risks ?? []) : []"
          :reason="!intel?.risk.available ? getBlockReasonLabel(intel?.risk.reason_code ?? 'not_found') : null"
        />
      </div>

      <section class="panel">
        <div class="chart-head">
          <h3>日K（{{ days }}日）</h3>
          <div class="range">
            <button
              v-for="option in [20, 60, 120, 250]"
              :key="option"
              :class="{ active: days === option }"
              :aria-pressed="days === option"
              @click="days = option"
            >
              {{ option }}日
            </button>
          </div>
          <AdjustmentBadge
            v-if="chart"
            :adjustment="chart.adjustment"
          />
        </div>
        <p v-if="chart?.data_time" class="dim">行情截至 {{ chart.data_time }} · 历史日线</p>
        <p v-if="inconsistentTurnover" class="warn" role="status">部分历史成交量与成交额相互矛盾，相关量额待核验，暂不展示。</p>
        <p v-if="chartLoading" class="loading" role="status">正在加载{{ days }}日行情…</p>
        <p v-if="chartError" class="error-banner" role="alert">K线加载失败：{{ chartError }}</p>
        <p v-if="chart && chart.bars.length < days" class="dim">已显示可用的 {{ chart.bars.length }} 个交易日</p>
        <p v-if="showDegradedChartHint" class="warn-note">
          当前为{{ chart?.adjustment.effective_adjustment_type === "RAW" ? "不复权" : "非前复权" }}数据，除权日存在价格跳空
        </p>
        <p v-if="chart?.tail_fill && chart.tail_fill.triggered && !chart.tail_fill.ok" class="warn-note">
          行情截至 {{ chart.data_time }}，最新行情补齐失败
        </p>
        <BlockState
          v-if="!chartLoading && !chartError"
          :block="{
            available: !!chart && chart.bars.length > 0,
            reason_code: chart?.bars.length ? null : 'not_found',
            stale: false,
          }"
        >
          <template #fallback>
            <p class="dim">暂无K线数据</p>
          </template>
          <KlineChart :bars="chart?.bars ?? []" :zones="zonePacket" />
        </BlockState>
        <ProvenanceLine
          v-if="chart"
          :source="chart.data_source"
          :data-time="chart.data_time"
          :formal-status="chart.formal_status"
          :extra="[
            chart.market_coverage_date ? `全市场覆盖截止：${chart.market_coverage_date}` : '',
            chart.tail_fill?.ok === false && chart.tail_fill.reason_code === 'seam_rejected'
              ? '最新行情补齐失败（复权校验未通过，未拼接）'
              : '',
          ].filter(Boolean)"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.page-title {
  margin: 0 0 14px;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 16px;
}
@media (max-width: 1000px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}
.chart-head {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.stock-caption { font-size: 13px; font-weight: 400; color: var(--text-dim); margin-left: 8px; }
.range {
  display: flex;
  gap: 6px;
}
.range button {
  background: var(--bg-panel);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 3px 10px;
  cursor: pointer;
}
.range button.active {
  border-color: var(--accent);
  color: var(--accent);
}
.warn-note {
  color: var(--warn);
  font-size: 12px;
  margin: 8px 0 0;
}
.dim {
  color: var(--text-dim);
}
.error-banner {
  background: var(--bg-panel);
  border: 1px solid var(--veto);
  color: var(--veto);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 16px;
}
</style>
