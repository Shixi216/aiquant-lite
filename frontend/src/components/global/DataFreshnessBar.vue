<script setup lang="ts">
import { computed } from "vue";
import type { DashboardResponse } from "../../types/v2";
import {
  formatDate,
  formatDateTime,
  getTimeSemanticLabel,
} from "../../domain/labels";

const props = defineProps<{ timeSemantics: DashboardResponse["time_semantics"] }>();

const rows = computed(() =>
  Object.entries(props.timeSemantics)
    .filter(([, field]) => Boolean(field.value))
    .map(([key, field]) => ({
      key,
      label: getTimeSemanticLabel(key),
      display: key === "market_trade_date" ? formatDate(field.value!) : formatDateTime(field.value!),
      source: field.source,
    }))
);
</script>

<template>
  <div class="freshness-bar" aria-label="数据时点，统一显示北京时间">
    <small class="timezone-note">数据时点 · 北京时间</small>
    <div v-for="row in rows" :key="row.key" class="freshness-row">
      <span class="label">{{ row.label }}</span>
      <span class="value">{{ row.display }}</span>
      <span class="source" :title="row.source ?? undefined">数据来源</span>
    </div>
  </div>
</template>

<style scoped>
.freshness-bar {
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 16px;
}
.freshness-row {
  display: flex;
  gap: 10px;
  font-size: 12px;
  line-height: 1.7;
}
.label {
  width: 130px;
  flex-shrink: 0;
  color: var(--text-dim);
}
@media (max-width: 760px) { .source { display: none; } .freshness-row { flex-wrap: wrap; } }
.timezone-note { color: var(--text-dim); margin-bottom: 4px; }
.source {
  color: var(--text-dim);
  margin-left: auto;
}
</style>
