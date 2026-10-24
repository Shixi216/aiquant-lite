<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { StateDefinitions, TransitionRow } from "../../api/capitalBehavior";
import { useECharts } from "../../composables/useECharts";

const props = defineProps<{ rows: TransitionRow[]; definitions: StateDefinitions }>();

const chartEl = ref<HTMLElement | null>(null);
const { setOption } = useECharts(chartEl);

const stateNames = computed(() => Object.keys(props.definitions.states));

function formatPct(value: unknown): string {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
}

const option = computed(() => {
  const names = stateNames.value;
  const maxProb = Math.max(...props.rows.map((row) => row.transition_probability), 0.01);
  return {
    tooltip: {
      formatter: (params: { dataIndex: number }) => {
        const row = props.rows[params.dataIndex];
        if (!row) return "";
        return [
          `<b>${row.from_state} → ${row.to_state}</b>`,
          `转移概率：${formatPct(row.transition_probability)}`,
          `样本转移次数：${row.transitions}（${row.source_dates} 个交易日）`,
          `1d 收益：${formatPct(row.mean_return_1d)}`,
          `5d 收益：${formatPct(row.mean_return_5d)}`,
          `20d 收益：${formatPct(row.mean_return_20d)}`,
          `20d 胜率：${formatPct(row.win_rate_20d)}`,
          `生命周期假设：${row.lifecycle_hypothesis ? "是" : "否"}`,
        ].join("<br/>");
      },
    },
    grid: { left: 170, right: 90, top: 12, bottom: 96 },
    xAxis: {
      type: "category",
      data: names,
      axisLabel: { color: "#8494a7", rotate: 45, fontSize: 10 },
      splitArea: { show: true },
    },
    yAxis: {
      type: "category",
      data: names,
      axisLabel: { color: "#8494a7", fontSize: 10 },
      splitArea: { show: true },
    },
    visualMap: {
      min: 0,
      max: Number(maxProb.toFixed(3)),
      calculable: true,
      orient: "vertical",
      right: 8,
      top: "center",
      textStyle: { color: "#8494a7" },
      formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
      inRange: { color: ["#10161f", "#1c4d80", "#2f7ed8", "#7fc3ff"] },
    },
    series: [
      {
        type: "heatmap",
        data: props.rows.map((row) => [
          names.indexOf(row.to_state),
          names.indexOf(row.from_state),
          row.transition_probability,
        ]),
        emphasis: { itemStyle: { borderColor: "#d8dee6", borderWidth: 1 } },
      },
    ],
  };
});

watch(option, (value) => setOption(value, true), { immediate: true, deep: true });
</script>

<template>
  <div class="panel">
    <h3>状态转移概率矩阵（行 = 来源状态，列 = 目标状态）</h3>
    <div ref="chartEl" style="height: 560px" />
  </div>
</template>
