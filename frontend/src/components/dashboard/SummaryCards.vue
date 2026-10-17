<script setup lang="ts">
import { computed } from "vue";
import type { DashboardResponse } from "../../types/v2";
import BlockState from "../global/BlockState.vue";
import {
  formatAmount,
  formatPct,
  NO_DATA,
  getBlockReasonLabel,
} from "../../domain/labels";

const props = defineProps<{ breadth: DashboardResponse["market_breadth"] }>();

const cards = computed(() => {
  const data = props.breadth.data;
  if (!props.breadth.available || !data) return [];
  return [
    { label: "上涨家数", value: String(data.advances), tone: "up" },
    { label: "下跌家数", value: String(data.declines), tone: "down" },
    { label: "平盘", value: String(data.flats), tone: "" },
    {
      label: "中位涨跌幅",
      value: data.median_change_pct === null ? NO_DATA : formatPct(data.median_change_pct),
      tone: (data.median_change_pct ?? 0) > 0 ? "up" : (data.median_change_pct ?? 0) < 0 ? "down" : "",
    },
    { label: "成交总额", value: formatAmount(data.total_amount), tone: "" },
    {
      label: "样本",
      value: data.partial_universe
        ? `不完整 ${data.received_symbol_count}/${data.expected_universe_size}`
        : `完整 ${data.received_symbol_count}/${data.expected_universe_size}`,
      tone: data.partial_universe ? "warn" : "",
    },
  ];
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
    <p v-if="breadth.data?.partial_universe" class="warn-note">
      市场宽度基于不完整样本（{{ breadth.data?.received_symbol_count }}/{{ breadth.data?.expected_universe_size }}）
    </p>
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
.card-value.warn { color: var(--warn); }
.warn-note {
  color: var(--warn);
  font-size: 12px;
  margin: 8px 0 0;
}
.dim {
  color: var(--text-dim);
}
</style>
