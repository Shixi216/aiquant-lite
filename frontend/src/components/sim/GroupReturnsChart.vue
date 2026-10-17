<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { SnapshotRow } from "../../api/simDaily";
import { useECharts } from "../../composables/useECharts";

const props = defineProps<{ rows: SnapshotRow[] }>();

const chartEl = ref<HTMLElement | null>(null);
const { setOption } = useECharts(chartEl);

const option = computed(() => {
  const groups = ["命中组", "临界组", "对照组"];
  const means = groups.map((group) => {
    const values = props.rows
      .filter((row) => row.观察组 === group && typeof row.后续表现?.涨跌幅 === "number")
      .map((row) => row.后续表现?.涨跌幅 ?? 0);
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
  });
  return {
    title: { text: "分组平均涨跌幅（%）", textStyle: { color: "#8494a7", fontSize: 13 } },
    tooltip: { trigger: "axis" },
    grid: { left: 48, right: 20, top: 44, bottom: 28 },
    xAxis: { type: "category", data: groups, axisLabel: { color: "#8494a7" } },
    yAxis: { type: "value", axisLabel: { color: "#8494a7", formatter: "{value}%" } },
    series: [
      {
        type: "bar",
        data: means.map((value) => ({
          value: Number(value.toFixed(3)),
          itemStyle: { color: value > 0 ? "#f04848" : value < 0 ? "#2ebd85" : "#8494a7" },
        })),
        barWidth: 42,
        label: { show: true, position: "top", color: "#d8dee6", formatter: "{c}%" },
      },
    ],
  };
});

watch(option, (value) => setOption(value, true), { immediate: true, deep: true });
</script>

<template>
  <div class="panel">
    <div ref="chartEl" style="height: 260px" />
  </div>
</template>
