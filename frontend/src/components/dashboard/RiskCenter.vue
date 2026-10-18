<script setup lang="ts">
import { computed } from "vue";
import type { DashboardResponse } from "../../types/v2";
import BlockState from "../global/BlockState.vue";
import { formatDate, getRiskLabel, getComponentLabel, getHealthStatusLabel } from "../../domain/labels";
void formatDate;

const props = defineProps<{
  coverage: DashboardResponse["coverage"];
  breadth: DashboardResponse["market_breadth"];
  components: DashboardResponse["components"];
}>();

const risks = computed(() => {
  const list: { code: string; text: string }[] = [];
  const coverageData = props.coverage.data as { data_cutoff?: string } | null;
  if (!props.coverage.available) {
    list.push({ code: "stale_data", text: `覆盖报告不可用` });
  } else if (coverageData?.data_cutoff) {
    const cutoff = coverageData.data_cutoff.slice(0, 10);
    list.push({
      code: "stale_data",
      text: `覆盖报告截至 ${cutoff}${props.coverage.stale ? "（数据过期）" : ""}`,
    });
  }
  const breadthData = props.breadth.data;
  if (breadthData?.partial_universe) {
    list.push({ code: "partial_universe", text: getRiskLabel("partial_universe") });
  }
  if (props.breadth.stale) {
    list.push({ code: "stale_data", text: "行情快照已过期，请核对业务交易日与来源状态" });
  }
  const unhealthy = props.components.filter((chip) => !chip.healthy);
  for (const chip of unhealthy) {
    list.push({ code: "stale_data", text: `${getComponentLabel(chip.name)}：${getHealthStatusLabel(chip.status)}` });
  }
  if (!list.length) list.push({ code: "NO_RISK", text: getRiskLabel("NO_RISK") });
  return list;
});
</script>

<template>
  <section class="panel">
    <h3>风险中心</h3>
    <BlockState :block="{ available: true, reason_code: null, stale: false }">
      <ul class="risk-list">
        <li v-for="risk in risks" :key="risk.code + risk.text" :class="{ warn: risk.code !== 'NO_RISK' }">
          {{ risk.text }}
        </li>
      </ul>
    </BlockState>
  </section>
</template>

<style scoped>
.risk-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.risk-list li.warn {
  color: var(--warn);
}
</style>
