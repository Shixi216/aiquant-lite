<script setup lang="ts">
import { computed } from "vue";
import type { DashboardResponse } from "../../types/v2";
import BlockState from "../global/BlockState.vue";
import {
  formatAmount,
  formatPct,
  getBlockReasonLabel,
} from "../../domain/labels";
import { acceptedMarketBreadth, marketCoverageLabel } from "../../domain/dashboardDisplay";

const props = defineProps<{ breadth: DashboardResponse["market_breadth"] }>();

const cards = computed(() => {
  const data = props.breadth.data;
  if (!props.breadth.available || !data) return [];
  const result = [
    { label: "上涨家数", value: String(data.advances), tone: "up" },
    { label: "下跌家数", value: String(data.declines), tone: "down" },
    { label: "平盘", value: String(data.flats), tone: "" },
  ];
  if (data.median_change_pct !== null) result.push({
      label: "中位涨跌幅",
      value: formatPct(data.median_change_pct),
      tone: (data.median_change_pct ?? 0) > 0 ? "up" : (data.median_change_pct ?? 0) < 0 ? "down" : "",
  });
  if (data.total_amount !== null) result.push({ label: "成交总额", value: formatAmount(data.total_amount), tone: "" });
  if (acceptedMarketBreadth(data)) {
    result.push({ label: "市场覆盖", value: marketCoverageLabel(data), tone: "" });
  }
  return result;
});
</script>

<template>
  <BlockState :block="breadth">
    <template #fallback>
      <p class="dim">市场宽度不可用：{{ getBlockReasonLabel(breadth?.reason_code) }}</p>
    </template>
    <div class="cards">
      <div v-for="card in cards" :key="card.label" class="card">
        <span class="card-label">{{ card.label }}</span>
        <span :class="['card-value', card.tone]">{{ card.value }}</span>
      </div>
    </div>
  </BlockState>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}
.card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.card-label {
  font-size: 12px;
  color: var(--text-dim);
}
.card-value {
  font-size: 18px;
  font-weight: 600;
}
.card-value.up { color: var(--up); }
.card-value.down { color: var(--down); }
.dim {
  color: var(--text-dim);
}
</style>
