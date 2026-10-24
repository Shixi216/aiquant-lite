<script setup lang="ts">
import { computed, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type { MarketBreadthData } from "../../types/v2";
import echarts, { type EChartsCoreOption } from "../../charts";
import { useECharts } from "../../composables/useECharts";
import { useChartTheme } from "../../domain/chartTheme";
import { DOWN_COLOR, UP_COLOR } from "../../domain/labels";

const props = defineProps<{ breadth: MarketBreadthData | null }>();

const el = ref<HTMLElement | null>(null);
const { setOption } = useECharts(el as Ref<HTMLElement | null>);
const ct = useChartTheme();

const option = computed<EChartsCoreOption | null>(() => {
  const data = props.breadth;
  if (!data) return null;
  const buckets = data.change_buckets;
  const categories = [
    ">7%", "5~7%", "2~5%", "0~2%", "平盘", "-2~0%", "-5~-2%", "-7~-5%", "<-7%",
  ];
  const values = [
    buckets.gt7, buckets.p5_7, buckets.p2_5, buckets.p0_2, buckets.flat,
    buckets.n0_2, buckets.n2_5, buckets.n5_7, buckets.lt7,
  ];
  const colors = [
    UP_COLOR, UP_COLOR, UP_COLOR, UP_COLOR, ct.value.dim,
    DOWN_COLOR, DOWN_COLOR, DOWN_COLOR, DOWN_COLOR,
  ];
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 50, right: 16, top: 24, bottom: 28 },
    xAxis: { type: "category", data: categories },
    yAxis: { type: "value", name: "家数" },
    series: [
      {
        type: "bar",
        data: values.map((value, index) => ({ value, itemStyle: { color: colors[index] } })),
      },
    ],
  } as EChartsCoreOption;
});

watchEffect(() => { if (option.value) setOption(option.value); });
</script>

<template>
  <section class="panel">
    <h3>市场涨跌分布</h3>
    <div v-if="breadth" ref="el" class="chart"></div>
    <p v-else class="dim">暂无数据</p>
  </section>
</template>

<style scoped>
.chart {
  height: 280px;
}
.dim {
  color: var(--text-dim);
}
</style>
