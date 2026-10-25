<script setup lang="ts">
import { computed, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type { MarginPoint } from "../../types/v2";
import type { EChartsCoreOption } from "../../charts";
import { useECharts } from "../../composables/useECharts";
import { useChartTheme } from "../../domain/chartTheme";
import { formatAmountYi } from "../../domain/labels";

const props = defineProps<{ points: MarginPoint[] }>();

const el = ref<HTMLElement | null>(null);
const { setOption } = useECharts(el as Ref<HTMLElement | null>);
const ct = useChartTheme();

const option = computed<EChartsCoreOption | null>(() => {
  const data = props.points;
  if (!data.length) return null;
  const dates = data.map((p) => p.trade_date.slice(5));
  const values = data.map((p) => (p.margin_total === null ? null : p.margin_total / 1e8));
  const last = values[values.length - 1];
  return {
    animation: false,
    tooltip: { trigger: "axis", valueFormatter: (v: number) => `${formatAmountYi(v * 1e8)} 元` },
    grid: { left: 64, right: 20, top: 18, bottom: 26 },
    xAxis: { type: "category", data: dates, boundaryGap: false, axisLabel: { fontSize: 10 } },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: {
        fontSize: 10,
        formatter: (v: number) => `${(v / 1e4).toFixed(1)}万亿`,
      },
      splitLine: { lineStyle: { color: "rgba(38,49,64,0.5)" } },
    },
    series: [
      {
        name: "两融余额",
        type: "line",
        data: values,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2.2, color: ct.value.gold },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(232,180,90,0.32)" },
              { offset: 1, color: "rgba(232,180,90,0.02)" },
            ],
          },
        },
        markPoint: last
          ? {
              data: [{ type: "max", name: "峰值" }],
              symbolSize: 40,
              label: { fontSize: 9 },
            }
          : undefined,
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
  height: 240px;
}
</style>
