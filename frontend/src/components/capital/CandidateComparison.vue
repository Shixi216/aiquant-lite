<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { CandidatesPayload } from "../../api/capitalBehavior";
import { useECharts } from "../../composables/useECharts";
import { getStrategyRoleLabel } from "../../domain/labels";

const props = defineProps<{ payload: CandidatesPayload }>();

const chartEl = ref<HTMLElement | null>(null);
const { setOption } = useECharts(chartEl);

const METRIC_LABELS: [keyof CandidateChartValue, string, (value: number) => string][] = [
  ["annualized_return", "年化收益", (v) => `${(v * 100).toFixed(1)}%`],
  ["sharpe", "夏普比率", (v) => v.toFixed(3)],
  ["calmar", "卡玛比率", (v) => v.toFixed(3)],
  ["max_drawdown", "最大回撤", (v) => `${(v * 100).toFixed(1)}%`],
  ["tail_loss", "尾部损失", (v) => `${(v * 100).toFixed(1)}%`],
];

type CandidateChartValue = {
  annualized_return: number;
  sharpe: number;
  calmar: number;
  max_drawdown: number;
  tail_loss: number;
};

const option = computed(() => {
  const labels = METRIC_LABELS.map(([, label]) => label);
  const strategies = [
    { id: `${props.payload.baseline.candidate_id}（基准）`, metrics: props.payload.baseline.metrics, color: "#8ca0b3" },
    ...props.payload.research_candidates.map((candidate) => ({
      id: candidate.candidate_id,
      metrics: candidate.metrics,
      color: candidate.promotion_passed ? "#2ebd85" : "#4da3ff",
    })),
  ];
  return {
    tooltip: {
      trigger: "axis",
      formatter: (params: Array<{ seriesName: string; dataIndex: number; value: number }>) =>
        params
          .map(
            (item) =>
              `${item.seriesName}：${METRIC_LABELS[item.dataIndex]?.[2](item.value) ?? item.value}`,
          )
          .join("<br/>"),
    },
    legend: { data: strategies.map((item) => item.id), textStyle: { color: "#8494a7" }, top: 0 },
    grid: { left: 48, right: 20, top: 40, bottom: 28 },
    xAxis: { type: "category", data: labels, axisLabel: { color: "#8494a7" } },
    yAxis: { type: "value", axisLabel: { color: "#8494a7" } },
    series: strategies.map((item) => ({
      name: item.id,
      type: "bar" as const,
      data: METRIC_LABELS.map(([key]) => Number(item.metrics[key].toFixed(4))),
      itemStyle: { color: item.color },
      barMaxWidth: 26,
    })),
  };
});

watch(option, (value) => setOption(value, true), { immediate: true, deep: true });
</script>

<template>
  <div class="panel">
    <h3>候选策略对比（基准 vs 研究候选）</h3>
    <div ref="chartEl" style="height: 320px" />
    <div class="candidate-list">
      <div v-for="candidate in payload.research_candidates" :key="candidate.candidate_id" class="candidate-item">
        <div class="candidate-head">
          <strong>{{ candidate.candidate_id }}</strong>
          <span class="badge">{{ getStrategyRoleLabel(candidate.role) }}</span>
          <span class="badge" :class="candidate.promotion_passed ? 'ok' : 'veto'">
            {{ candidate.promotion_passed ? "晋升通过" : "未晋升" }}
          </span>
          <span class="badge">成本 {{ candidate.cost_bp }}bp</span>
        </div>
        <div class="candidate-meta">
          参数：
          <code v-for="(value, key) in candidate.parameters" :key="key">{{ key }}={{ value }}</code>
          <span v-if="candidate.metrics.year_2026_return !== undefined">
            · 2026 年内收益 {{ (candidate.metrics.year_2026_return * 100).toFixed(1) }}%
          </span>
        </div>
        <div v-if="candidate.failed_rules.length" class="candidate-meta">
          未通过规则：
          <span v-for="rule in candidate.failed_rules" :key="rule" class="badge veto">{{ rule }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.candidate-list {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.candidate-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  display: grid;
  gap: 6px;
}

.candidate-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.candidate-meta {
  font-size: 12px;
  color: var(--text-dim);
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
</style>
