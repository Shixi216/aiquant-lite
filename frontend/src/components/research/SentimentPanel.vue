<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import type { DecayCurvePoint } from "../../api/research";
import {
  fetchDecayCurve,
  fetchIcSummary,
  fetchSentimentRuns,
  fetchStratifiedReturns,
  type SentimentRun,
} from "../../api/research";
import { useECharts } from "../../composables/useECharts";
import { getAlphaConclusionLabel } from "../../domain/labels";

const decayEl = ref<HTMLElement | null>(null);
const icEl = ref<HTMLElement | null>(null);
const stratEl = ref<HTMLElement | null>(null);
const decayChart = useECharts(decayEl);
const icChart = useECharts(icEl);
const stratChart = useECharts(stratEl);

const runs = ref<SentimentRun[]>([]);
const run = ref("");
const decay = ref<DecayCurvePoint[]>([]);
const icRows = ref<Record<string, unknown>[]>([]);
const stratRows = ref<Record<string, unknown>[]>([]);
const horizon = ref(1);
const error = ref<string | null>(null);
const loading = ref(false);

const horizons = computed(() =>
  [...new Set(decay.value.map((point) => point.horizon))].sort((a, b) => a - b),
);

const stratForHorizon = computed(() =>
  stratRows.value
    .filter((row) => Number(row.horizon) === horizon.value)
    .sort((a, b) => String(a.group).localeCompare(String(b.group))),
);

function decayOption() {
  const methods = [...new Set(decay.value.map((point) => point.score_method))];
  const hs = horizons.value;
  return {
    title: { text: "IC 衰减曲线", textStyle: { color: "#8494a7", fontSize: 13 }, top: 0 },
    tooltip: { trigger: "axis" },
    legend: { data: methods, textStyle: { color: "#8494a7" }, top: 22 },
    grid: { left: 56, right: 56, top: 56, bottom: 28 },
    xAxis: { type: "category", data: hs.map((h) => `${h}d`), axisLabel: { color: "#8494a7" } },
    yAxis: [
      { type: "value", name: "平均 IC", axisLabel: { color: "#8494a7" } },
      { type: "value", name: "信息比率 (IC_IR)", axisLabel: { color: "#8494a7" } },
    ],
    series: methods.flatMap((method) => {
      const points = hs.map((h) =>
        decay.value.find((point) => point.score_method === method && point.horizon === h),
      );
      return [
        {
          name: method,
          type: "line",
          data: points.map((point) => point?.mean_ic ?? null),
          itemStyle: { color: "#4da3ff" },
          smooth: true,
        },
        {
          name: `${method} IR`,
          type: "line",
          yAxisIndex: 1,
          data: points.map((point) => point?.ic_ir ?? null),
          itemStyle: { color: "#e6a23c" },
          smooth: true,
          lineStyle: { type: "dashed" },
        },
      ];
    }),
  };
}

function icOption() {
  const hs = horizons.value;
  const methods = [...new Set(icRows.value.map((row) => String(row.score_method)))];
  return {
    title: { text: "IC 矩阵（score_method × horizon，mean_ic）", textStyle: { color: "#8494a7", fontSize: 13 }, top: 0 },
    tooltip: {
      formatter: (params: { dataIndex: number }) => {
        const row = icRows.value[params.dataIndex];
        if (!row) return "";
        return [
          `<b>${row.score_method} · ${row.horizon}d</b>`,
          `mean_ic：${Number(row.mean_ic ?? 0).toFixed(4)}`,
          `ic_ir：${Number(row.ic_ir ?? 0).toFixed(3)}`,
          `正 IC 占比：${Number(row.positive_ic_ratio ?? 0).toFixed(3)}`,
          `样本：${row.sample_count}`,
        ].join("<br/>");
      },
    },
    grid: { left: 120, right: 80, top: 48, bottom: 40 },
    xAxis: { type: "category", data: hs.map((h) => `${h}d`), axisLabel: { color: "#8494a7" } },
    yAxis: { type: "category", data: methods, axisLabel: { color: "#8494a7" } },
    visualMap: {
      min: 0,
      max: Math.max(...icRows.value.map((row) => Number(row.mean_ic ?? 0)), 0.01),
      calculable: true,
      orient: "vertical",
      right: 4,
      top: "center",
      textStyle: { color: "#8494a7" },
      inRange: { color: ["#10161f", "#1c4d80", "#2f7ed8", "#7fc3ff"] },
    },
    series: [
      {
        type: "heatmap",
        data: icRows.value.map((row) => [
          hs.indexOf(Number(row.horizon)),
          methods.indexOf(String(row.score_method)),
          Number(row.mean_ic ?? 0),
        ]),
      },
    ],
  };
}

function stratOption() {
  const rows = stratForHorizon.value;
  return {
    title: { text: `分层累计收益（horizon=${horizon.value}d）`, textStyle: { color: "#8494a7", fontSize: 13 }, top: 0 },
    tooltip: { trigger: "axis" },
    grid: { left: 56, right: 20, top: 44, bottom: 32 },
    xAxis: { type: "category", data: rows.map((row) => String(row.group)), axisLabel: { color: "#8494a7", rotate: 30 } },
    yAxis: { type: "value", axisLabel: { color: "#8494a7", formatter: "{value}%" } },
    series: [
      {
        type: "bar",
        data: rows.map((row) => ({
          value: Number((Number(row.cumulative_return ?? 0) * 100).toFixed(2)),
          itemStyle: { color: Number(row.cumulative_return ?? 0) >= 0 ? "#f04848" : "#2ebd85" },
        })),
        barMaxWidth: 30,
      },
    ],
  };
}

function redraw() {
  decayChart.setOption(decayOption(), true);
  icChart.setOption(icOption(), true);
  stratChart.setOption(stratOption(), true);
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const runList = await fetchSentimentRuns();
    runs.value = runList.runs;
    if (!run.value && runs.value.length) run.value = runs.value[0].run;
    if (run.value) {
      const [curve, ic, strat] = await Promise.all([
        fetchDecayCurve(run.value),
        fetchIcSummary(run.value),
        fetchStratifiedReturns(run.value),
      ]);
      decay.value = curve.curves;
      icRows.value = ic.rows;
      stratRows.value = strat.rows;
      if (!horizons.value.includes(horizon.value)) horizon.value = horizons.value[0] ?? 1;
      redraw();
    }
  } catch (err) {
    error.value = String(err);
  } finally {
    loading.value = false;
  }
}

watch(run, load);
watch(horizon, () => stratChart.setOption(stratOption(), true));
onMounted(load);
</script>

<template>
  <div class="toolbar">
    <select v-model="run">
      <option v-for="item in runs" :key="item.run" :value="item.run">{{ item.run }}</option>
    </select>
    <span v-for="item in runs" v-show="item.run === run" :key="item.run" class="badge">
      {{ getAlphaConclusionLabel(item.alpha_conclusion) }} · {{ item.date_from }} → {{ item.date_to }} · {{ item.date_count }} 交易日
    </span>
    <select v-model="horizon">
      <option v-for="h in horizons" :key="h" :value="h">{{ h }}d</option>
    </select>
    <span v-if="loading" class="badge">加载中…</span>
  </div>
  <div v-if="error" class="error-box">{{ error }}</div>
  <template v-else>
    <div class="panel"><div ref="decayEl" style="height: 320px" /></div>
    <div class="panel"><div ref="icEl" style="height: 300px" /></div>
    <div class="panel"><div ref="stratEl" style="height: 300px" /></div>
  </template>
</template>
