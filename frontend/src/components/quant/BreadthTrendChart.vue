<script setup lang="ts">
import { computed, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type { BreadthPoint } from "../../types/v2";
import echarts, { type EChartsCoreOption } from "../../charts";
import { useECharts } from "../../composables/useECharts";
import { useChartTheme } from "../../domain/chartTheme";
import { DOWN_COLOR, UP_COLOR } from "../../domain/labels";

const props = defineProps<{ points: BreadthPoint[] }>();

const el = ref<HTMLElement | null>(null);
const { setOption } = useECharts(el as Ref<HTMLElement | null>);
const ct = useChartTheme();

const option = computed<EChartsCoreOption | null>(() => {
  const data = props.points;
  if (!data.length) return null;
  const dates = data.map((p) => p.trade_date.slice(5));
  return {
    animation: false,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      valueFormatter: undefined,
    },
    legend: { data: ["上涨占比", "涨停家数", "跌停家数"], top: 0, textStyle: { fontSize: 11 } },
    grid: [
      { left: 52, right: 56, top: 30, height: "56%" },
      { left: 52, right: 56, top: "74%", height: "17%" },
    ],
    xAxis: [
      { type: "category", data: dates, gridIndex: 0, boundaryGap: false, axisLabel: { show: false } },
      { type: "category", data: dates, gridIndex: 1, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      {
        type: "value", gridIndex: 0, min: 0, max: 1,
        axisLabel: { formatter: (v: number) => `${Math.round(v * 100)}%`, fontSize: 10 },
        splitLine: { lineStyle: { color: "rgba(38,49,64,0.5)" } },
      },
      { type: "value", gridIndex: 1, axisLabel: { fontSize: 10 }, splitLine: { show: false } },
    ],
    series: [
      {
        name: "上涨占比",
        type: "line",
        data: data.map((p) => p.advance_ratio),
        xAxisIndex: 0,
        yAxisIndex: 0,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2.2, color: ct.value.accent },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(77,163,255,0.35)" },
              { offset: 1, color: "rgba(77,163,255,0.02)" },
            ],
          },
        },
      },
      {
        name: "涨停家数",
        type: "bar",
        data: data.map((p) => p.limit_ups),
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: { color: UP_COLOR, borderRadius: [2, 2, 0, 0] },
        barMaxWidth: 8,
      },
      {
        name: "跌停家数",
        type: "bar",
        data: data.map((p) => p.limit_downs),
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: { color: DOWN_COLOR, borderRadius: [2, 2, 0, 0] },
        barMaxWidth: 8,
      },
    ],
  } as EChartsCoreOption;
});

watchEffect(() => setOption(option.value, true));
</script>

<template>
  <div ref="el" class="chart"></div>
</template>

<style scoped>
.chart {
  height: 320px;
}
</style>
