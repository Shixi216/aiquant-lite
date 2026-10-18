<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import type { CostRow, WalkForwardRow, YearlyRow } from "../../api/research";
import { fetchStrategyLab } from "../../api/research";
import { useECharts } from "../../composables/useECharts";
import { errorMessage } from "../../api/client";
import { getCandidateLabel, getWalkForwardLabel } from "../../domain/labels";

const foldsEl = ref<HTMLElement | null>(null);
const costEl = ref<HTMLElement | null>(null);
const foldsChart = useECharts(foldsEl);
const costChart = useECharts(costEl);

const walkForward = ref<WalkForwardRow[]>([]);
const yearly = ref<YearlyRow[]>([]);
const cost = ref<CostRow[]>([]);
const candidate = ref("");
const error = ref<string | null>(null);
const loading = ref(false);

const candidates = computed(() =>
  [...new Set([
    ...walkForward.value.map((row) => row.candidate_id),
    ...cost.value.map((row) => row.candidate_id),
  ])],
);

const selectedFolds = computed(() =>
  walkForward.value
    .filter((row) => row.candidate_id === candidate.value)
    .sort((a, b) => a.train_window_days - b.train_window_days || a.fold - b.fold),
);

const selectedCost = computed(() =>
  cost.value
    .filter((row) => row.candidate_id === candidate.value)
    .sort((a, b) => a.cost_bp - b.cost_bp),
);

const yearlyForCandidate = computed(() =>
  yearly.value.filter((row) => row.candidate_id === candidate.value),
);

function num(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function foldsOption() {
  const rows = selectedFolds.value;
  return {
    title: { text: "滚动样本外验证", textStyle: { color: "#8494a7", fontSize: 13 }, top: 0 },
    tooltip: { trigger: "axis" },
    legend: { data: rows.some((row) => num(row.sortino) !== null) ? ["夏普比率", "索提诺比率"] : ["夏普比率"], textStyle: { color: "#8494a7" }, top: 22 },
    grid: { left: 48, right: 48, top: 56, bottom: 48 },
    xAxis: { type: "category", data: rows.map(getWalkForwardLabel), axisLabel: { color: "#8494a7", fontSize: 10, rotate: 15 } },
    yAxis: [{ type: "value", axisLabel: { color: "#8494a7" } }, { type: "value", axisLabel: { color: "#8494a7" } }],
    series: [
      {
        name: "夏普比率",
        type: "bar",
        data: rows.map((row) => num(row.sharpe)),
        itemStyle: { color: "#4da3ff" },
        barMaxWidth: 30,
      },
      {
        name: "索提诺比率",
        type: "line",
        yAxisIndex: 1,
        data: rows.map((row) => num(row.sortino)),
        itemStyle: { color: "#e6a23c" },
        smooth: true,
      },
    ],
  };
}

function costOption() {
  const rows = selectedCost.value;
  return {
    title: { text: "成本敏感性", textStyle: { color: "#8494a7", fontSize: 13 }, top: 0 },
    tooltip: { trigger: "axis" },
    legend: { data: ["夏普比率", "最大回撤%"], textStyle: { color: "#8494a7" }, top: 22 },
    grid: { left: 48, right: 56, top: 56, bottom: 28 },
    xAxis: { type: "category", data: rows.map((row) => `${row.cost_bp}bp`), axisLabel: { color: "#8494a7" } },
    yAxis: [
      { type: "value", axisLabel: { color: "#8494a7" } },
      { type: "value", axisLabel: { color: "#8494a7", formatter: "{value}%" } },
    ],
    series: [
      {
        name: "夏普比率",
        type: "line",
        data: rows.map((row) => num(row.sharpe)),
        itemStyle: { color: "#4da3ff" },
        smooth: true,
      },
      {
        name: "最大回撤%",
        type: "line",
        yAxisIndex: 1,
        data: rows.map((row) => (num(row.max_drawdown) === null ? null : Number((num(row.max_drawdown) as number * 100).toFixed(2)))),
        itemStyle: { color: "#f04848" },
        smooth: true,
      },
    ],
  };
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [wf, yr, cs] = await Promise.all([
      fetchStrategyLab("walk-forward"),
      fetchStrategyLab("yearly"),
      fetchStrategyLab("cost-sensitivity"),
    ]);
    walkForward.value = wf.rows as unknown as WalkForwardRow[];
    yearly.value = yr.rows as unknown as YearlyRow[];
    cost.value = cs.rows as unknown as CostRow[];
    if (!candidate.value && candidates.value.length) candidate.value = candidates.value[0];
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

watch([selectedFolds, selectedCost], () => {
  foldsChart.setOption(foldsOption(), true);
  costChart.setOption(costOption(), true);
});

watch(candidate, () => {
  foldsChart.setOption(foldsOption(), true);
  costChart.setOption(costOption(), true);
});

onMounted(load);
</script>

<template>
  <div class="toolbar">
    <select v-model="candidate" aria-label="研究候选策略">
      <option v-for="name in candidates" :key="name" :value="name">{{ getCandidateLabel(name) }}</option>
    </select>
    <span v-if="loading" class="badge">加载中…</span>
  </div>
  <div v-if="error" class="error-box">{{ error }}</div>
  <template v-else>
    <div class="panel">
      <div ref="foldsEl" style="height: 320px" />
    </div>
    <div class="panel">
      <div ref="costEl" style="height: 320px" />
    </div>
    <div class="panel">
      <h3>年度指标</h3>
      <table class="data">
        <thead>
          <tr>
            <th>年度</th>
            <th class="num">年化收益</th>
            <th class="num">夏普比率</th>
            <th class="num">索提诺比率</th>
            <th class="num">最大回撤</th>
            <th class="num">卡玛比率</th>
            <th class="num">胜率</th>
            <th class="num">盈亏比</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in yearlyForCandidate" :key="row.period">
            <td>{{ row.period }}</td>
            <td class="num" :class="(row.annualized_return ?? 0) > 0 ? 'up' : 'down'">
              {{ row.annualized_return === null ? "—" : `${(row.annualized_return * 100).toFixed(1)}%` }}
            </td>
            <td class="num">{{ row.sharpe?.toFixed(3) ?? "—" }}</td>
            <td class="num">{{ row.sortino?.toFixed(3) ?? "—" }}</td>
            <td class="num down">{{ row.max_drawdown === null ? "—" : `${(row.max_drawdown * 100).toFixed(1)}%` }}</td>
            <td class="num">{{ row.calmar?.toFixed(2) ?? "—" }}</td>
            <td class="num">{{ row.win_rate === null ? "—" : `${(row.win_rate * 100).toFixed(1)}%` }}</td>
            <td class="num">{{ row.profit_factor?.toFixed(2) ?? "—" }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!yearlyForCandidate.length" class="empty">该候选无年度指标。</div>
    </div>
  </template>
</template>
