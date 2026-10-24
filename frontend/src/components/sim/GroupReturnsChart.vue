<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { SnapshotRow } from "../../api/simDaily";
import { useECharts } from "../../composables/useECharts";
import { useChartTheme } from "../../domain/chartTheme";

const props = defineProps<{ rows: SnapshotRow[] }>();

const chartEl = ref<HTMLElement | null>(null);
const { setOption } = useECharts(chartEl);
const ct = useChartTheme();

const option = computed(() => {
  const groups = ["命中组", "临界组", "对照组"];
  const means = groups.map((group) => {
    const values = props.rows
      .filter((row) => row.观察组 === group && typeof row.后续表现?.涨跌幅 === "number")
      .map((row) => row.后续表现?.涨跌幅 ?? 0);
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
  });
  return {
    title: { text: "分组平均涨跌幅（%）", textStyle: { color: ct.value.dim, fontSize: 13 } },
    tooltip: { trigger: "axis" },
    grid: { left: 48, right: 20, top: 44, bottom: 28 },
    xAxis: { type: "category", data: groups, axisLabel: { color: ct.value.dim } },
    yAxis: { type: "value", axisLabel: { color: ct.value.dim, formatter: "{value}%" } },
    series: [
      {
        type: "bar",
        data: means.map((value) => ({
          value: Number(value.toFixed(3)),
          itemStyle: { color: value > 0 ? ct.value.up : value < 0 ? ct.value.down : ct.value.dim },
        })),
        barWidth: 42,
        label: { show: true, position: "top", color: ct.value.text, formatter: "{c}%" },
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
