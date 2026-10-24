<script setup lang="ts">
/**
 * 多策略对比与组合优化（v2）。
 * POST /strategies/compare 统一口径比较矩阵 + 分组条形图；
 * POST /optimization/run（min_vol/max_sharpe/hrp）优化权重环形图 + 估计审计。
 */
import { computed, onMounted, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type {
  CompareData,
  OptimizationData,
  StrategiesData,
  StrategyCompareRow,
  WealthEnvelope,
} from "../types/wealthLabV2";
import type { OptimizationMethod } from "../types/wealthLabV2";
import { fetchV2Strategies, runV2Compare, runV2Optimization } from "../api/wealthLabV2";
import type { DataSource } from "../api/wealthLab";
import { errorMessage } from "../api/client";
import echarts, { type EChartsCoreOption } from "../charts";
import { useECharts } from "../composables/useECharts";
import { fmtPct, fmtRatio, returnColorClass } from "../domain/wealthFormat";
import { OPTIMIZATION_METHOD_LABELS } from "../domain/wealthV2Format";
import WlDateInput from "../components/wealth/WlDateInput.vue";

const strategiesEnvelope = ref<WealthEnvelope<StrategiesData> | null>(null);
const baseSource = ref<DataSource>("live");
const baseError = ref<string | null>(null);
const strategies = computed(() => strategiesEnvelope.value?.data.strategies ?? []);

function strategyName(id: string): string {
  return strategies.value.find((s) => s.strategy_id === id)?.name ?? id;
}

/* ---------------- 比较表单与结果 ---------------- */

const selectedIds = ref<string[]>(["S2_cross_momentum", "S3_dual_momentum", "S4_inverse_volatility"]);
const compareForm = ref({ start_date: "2020-04-08", end_date: "2026-10-23", cost_bps: 5 });
const compareResult = ref<WealthEnvelope<CompareData> | null>(null);
const compareSource = ref<DataSource>("live");
const compareLoading = ref(false);
const compareError = ref<string | null>(null);

function toggleStrategy(id: string): void {
  const index = selectedIds.value.indexOf(id);
  if (index >= 0) {
    if (selectedIds.value.length > 1) selectedIds.value.splice(index, 1);
  } else {
    selectedIds.value.push(id);
  }
}

async function runCompare(): Promise<void> {
  compareLoading.value = true;
  compareError.value = null;
  try {
    const response = await runV2Compare({
      strategy_ids: [...selectedIds.value],
      start_date: compareForm.value.start_date,
      end_date: compareForm.value.end_date,
      cost_bps: compareForm.value.cost_bps,
    });
    compareResult.value = response.envelope;
    compareSource.value = response.source;
  } catch (err) {
    compareError.value = errorMessage(err);
  } finally {
    compareLoading.value = false;
  }
}

const comparison = computed(() => compareResult.value?.data.comparison ?? []);
/** 同区间统一口径下的交易日数（矩阵各行一致，取首行）。 */
const tradingDays = computed(() => comparison.value[0]?.metrics.trading_days ?? null);

/** 每列最优行索引：收益/夏普/Sortino/卡玛/月胜率取最大；波动取最小；回撤取最大（最接近 0）。 */
const bestIndices = computed<Record<string, number>>(() => {
  const rows = comparison.value;
  if (!rows.length) return {};
  const best: Record<string, number> = {};
  const pick = (key: keyof (typeof rows)[number]["metrics"], mode: "max" | "min") => {
    let target = 0;
    for (let i = 0; i < rows.length; i++) {
      const value = rows[i]!.metrics[key] as number;
      if (mode === "max" ? value > (rows[target]!.metrics[key] as number) : value < (rows[target]!.metrics[key] as number)) {
        target = i;
      }
    }
    best[key as string] = target;
  };
  pick("annual_return", "max");
  pick("sharpe_ratio", "max");
  pick("sortino_ratio", "max");
  pick("calmar_ratio", "max");
  pick("monthly_win_rate", "max");
  pick("annual_volatility", "min");
  pick("max_drawdown", "max");
  return best;
});

const metricColumns = [
  { key: "total_return", label: "累计收益", fmt: (v: number) => fmtPct(v) },
  { key: "annual_return", label: "年化收益", fmt: (v: number) => fmtPct(v) },
  { key: "annual_volatility", label: "年化波动", fmt: (v: number) => fmtPct(v, 2, false) },
  { key: "sharpe_ratio", label: "夏普", fmt: (v: number) => fmtRatio(v) },
  { key: "sortino_ratio", label: "Sortino", fmt: (v: number) => fmtRatio(v) },
  { key: "calmar_ratio", label: "卡玛", fmt: (v: number) => fmtRatio(v) },
  { key: "max_drawdown", label: "最大回撤", fmt: (v: number) => fmtPct(v) },
  { key: "monthly_win_rate", label: "月胜率", fmt: (v: number) => fmtPct(v, 1, false) },
  { key: "avg_turnover_per_rebalance", label: "平均换手", fmt: (v: number) => fmtPct(v, 2, false) },
  { key: "trading_days", label: "交易日", fmt: (v: number) => String(v) },
] as const;

/* ---------------- 比较条形图 ---------------- */

const barEl = ref<HTMLElement | null>(null);
const barChart = useECharts(barEl as Ref<HTMLElement | null>);

const barOption = computed<EChartsCoreOption | null>(() => {
  const rows = comparison.value;
  if (!rows.length) return null;
  const names = rows.map((row) => strategyName(row.strategy_id));
  return {
    animationDuration: 600,
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(13,17,24,0.95)",
      borderColor: "#232c3b",
      textStyle: { color: "#dde4ee", fontSize: 13 },
    },
    legend: { top: 0, right: 0, icon: "roundRect", itemWidth: 12, itemHeight: 8, textStyle: { color: "#8595aa", fontSize: 12.5 } },
    grid: { left: 56, right: 20, top: 38, bottom: 34 },
    xAxis: {
      type: "category",
      data: names,
      axisLine: { lineStyle: { color: "#232c3b" } },
      axisLabel: { color: "#dde4ee", fontSize: 12, interval: 0, rotate: names.length > 4 ? 16 : 0 },
    },
    yAxis: [
      {
        type: "value",
        axisLabel: { color: "#8595aa", fontSize: 12, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
        splitLine: { lineStyle: { color: "rgba(38,49,64,0.5)" } },
      },
      {
        type: "value",
        axisLabel: { color: "#8595aa", fontSize: 12 },
        splitLine: { show: false },
      },
    ],
    series: [
      { name: "年化收益", type: "bar", data: rows.map((r) => r.metrics.annual_return), itemStyle: { color: "#f04848", borderRadius: [4, 4, 0, 0] }, barWidth: 14 },
      { name: "最大回撤", type: "bar", data: rows.map((r) => r.metrics.max_drawdown), itemStyle: { color: "#2ebd85", borderRadius: [4, 4, 0, 0] }, barWidth: 14 },
      { name: "夏普比率", type: "bar", yAxisIndex: 1, data: rows.map((r) => r.metrics.sharpe_ratio), itemStyle: { color: "#e8b45a", borderRadius: [4, 4, 0, 0] }, barWidth: 14 },
    ],
  } satisfies EChartsCoreOption;
});

watchEffect(() => {
  const opt = barOption.value;
  if (opt) barChart.setOption(opt, true);
});

/* ---------------- 组合优化 ---------------- */

const optMethod = ref<OptimizationMethod>("hrp");
/** 优化器三方法（统一中文名见 OPTIMIZATION_METHOD_LABELS）。 */
const OPT_METHODS: OptimizationMethod[] = ["min_vol", "max_sharpe", "hrp"];
const optForm = ref({ estimation_window_days: 252, asof: "", risk_free_rate: 0.02 });
const optResult = ref<WealthEnvelope<OptimizationData> | null>(null);
const optSource = ref<DataSource>("live");
const optLoading = ref(false);
const optError = ref<string | null>(null);

async function runOptimization(): Promise<void> {
  optLoading.value = true;
  optError.value = null;
  try {
    const payload: Record<string, unknown> = {
      method: optMethod.value,
      estimation_window_days: optForm.value.estimation_window_days,
      risk_free_rate: optForm.value.risk_free_rate,
      adapter: "auto",
    };
    if (optForm.value.asof) payload.asof = optForm.value.asof;
    const response = await runV2Optimization(payload as Parameters<typeof runV2Optimization>[0]);
    optResult.value = response.envelope;
    optSource.value = response.source;
  } catch (err) {
    optError.value = errorMessage(err);
  } finally {
    optLoading.value = false;
  }
}

const optData = computed(() => optResult.value?.data ?? null);

const donutEl = ref<HTMLElement | null>(null);
const donutChart = useECharts(donutEl as Ref<HTMLElement | null>);

const ASSET_COLORS: Record<string, string> = {
  "510300.SH": "#4da3ff",
  "511010.SH": "#22d3ee",
  "518880.SH": "#e8b45a",
  "511380.SH": "#b58cff",
};

const donutOption = computed<EChartsCoreOption | null>(() => {
  const weights = optData.value?.weights ?? {};
  const entries = Object.entries(weights);
  if (!entries.length) return null;
  return {
    animationDuration: 500,
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(13,17,24,0.94)",
      borderColor: "#232c3b",
      textStyle: { color: "#dde4ee", fontSize: 12 },
      formatter: (p: { name: string; value: number }) => `${strategyName(p.name)}<br/>权重 ${(p.value * 100).toFixed(1)}%`,
    },
    legend: { bottom: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10, textStyle: { color: "#8595aa", fontSize: 11.5 } },
    title: {
      text: OPTIMIZATION_METHOD_LABELS[optData.value?.method ?? ""] ?? optData.value?.method ?? "",
      subtext: `算法适配器：${optData.value?.adapter_used === "native" ? "内置纯 numpy 实现" : optData.value?.adapter_used ?? "—"}`,
      left: "center",
      top: "34%",
      itemGap: 6,
      textStyle: { color: "#dde4ee", fontSize: 16, fontWeight: 700 },
      subtextStyle: { color: "#8595aa", fontSize: 10.5 },
    },
    series: [
      {
        name: "优化权重",
        type: "pie",
        radius: ["52%", "74%"],
        center: ["50%", "46%"],
        padAngle: 2,
        itemStyle: { borderColor: "#11161f", borderWidth: 2, borderRadius: 6 },
        label: { show: false },
        labelLine: { show: false },
        data: entries.map(([symbol, weight]) => ({
          name: symbol,
          value: Math.round(weight * 10000) / 10000,
          itemStyle: { color: ASSET_COLORS[symbol] ?? "#8595aa" },
        })),
      },
    ],
  } satisfies EChartsCoreOption;
});

watchEffect(() => {
  const opt = donutOption.value;
  if (opt) donutChart.setOption(opt, true);
});

const optMetrics = computed(() => {
  const data = optData.value;
  if (!data) return [];
  return [
    { label: "估计年化收益（窗口内）", value: fmtPct(data.expected_annual_return) },
    { label: "估计年化波动", value: fmtPct(data.estimated_annual_volatility, 2, false) },
    { label: "估计夏普", value: fmtRatio(data.estimated_sharpe) },
    { label: "现金权重", value: fmtPct(data.cash_weight, 1, false) },
  ];
});

onMounted(async () => {
  try {
    const result = await fetchV2Strategies();
    strategiesEnvelope.value = result.envelope;
    baseSource.value = result.source;
  } catch (err) {
    baseError.value = errorMessage(err);
  }
  void runCompare();
  // 缺省自动运行一次 HRP 优化，保证首屏即有标准化目标权重结果
  void runOptimization();
});
</script>

<template>
  <div class="strategy-compare">
    <div class="page-head">
      <p class="page-sub head-sub">同区间 · 同成本 · 同年化口径的统一比较矩阵，叠加组合优化器的标准化目标权重</p>
      <div class="head-chips">
        <span v-if="baseSource === 'offline-mock' || compareSource === 'offline-mock'" class="badge warn">契约 Mock 样例</span>
        <span v-else-if="compareResult?.data.data_mode === 'REAL'" class="badge ok">真实数据</span>
        <span class="badge action">契约 wealth-lab/v2</span>
      </div>
    </div>

    <section class="panel" aria-label="比较条件">
      <h3>比较条件 <span class="h3-extra">至少选择 1 个策略；比较矩阵同区间同成本统一计算</span></h3>
      <div v-if="baseError" class="error-box">{{ baseError }}</div>
      <div class="chips-row">
        <button
          v-for="meta in strategies"
          :key="meta.strategy_id"
          type="button"
          class="chip"
          :class="{ active: selectedIds.includes(meta.strategy_id) }"
          @click="toggleStrategy(meta.strategy_id)"
        >{{ meta.name }}</button>
      </div>
      <div class="toolbar compare-toolbar">
        <WlDateInput v-model="compareForm.start_date" label="开始日期" />
        <WlDateInput v-model="compareForm.end_date" label="结束日期" />
        <label class="field"><span>单边成本（bps）</span><input v-model.number="compareForm.cost_bps" type="number" min="0" step="1" /></label>
        <button class="mini primary" type="button" :disabled="compareLoading" @click="runCompare">{{ compareLoading ? "比较中…" : "运行比较" }}</button>
      </div>
    </section>

    <section class="panel" aria-label="比较矩阵">
      <h3>
        多策略比较矩阵
        <span v-if="compareResult" class="h3-extra">{{ compareResult.data.window.start_date }} ~ {{ compareResult.data.window.end_date }} · {{ compareResult.data.window.cost_bps }} bps</span>
        <span v-if="compareResult?.data.data_mode === 'REAL'" class="badge ok">真实数据</span>
      </h3>
      <div v-if="compareError" class="error-box">{{ compareError }}</div>
      <div v-if="comparison.length" class="window-strip">
        <span><b>比较区间</b>{{ compareResult?.data.window.start_date }} ~ {{ compareResult?.data.window.end_date }}</span>
        <span><b>交易日数</b>{{ tradingDays }} 天</span>
        <span><b>单边成本</b>{{ compareResult?.data.window.cost_bps }} bps</span>
        <span class="dim">全部策略同区间、同成本、同年化口径统一计算，与其他核验口径不可混用</span>
      </div>
      <div v-if="comparison.length" class="table-scroll">
        <table class="data compare-table">
          <thead>
            <tr>
              <th>策略</th>
              <th v-for="col in metricColumns" :key="col.key" class="num">{{ col.label }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in comparison" :key="row.strategy_id">
              <td>
                <strong>{{ strategyName(row.strategy_id) }}</strong>
                <span class="sym-name">{{ row.strategy_id }}</span>
              </td>
              <td
                v-for="col in metricColumns"
                :key="col.key"
                class="num"
                :class="{ best: bestIndices[col.key] === rowIndex, down: col.key === 'max_drawdown' }"
              >{{ col.fmt(row.metrics[col.key as keyof typeof row.metrics] as number) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="comparison.length" class="chart-caption"><strong>收益-风险对比</strong><span>年化收益（红） / 最大回撤（绿） / 夏普（金，右轴）</span></div>
      <div v-show="comparison.length" ref="barEl" class="chart bar-chart"></div>
    </section>

    <section class="panel" aria-label="组合优化">
      <h3>
        组合优化器
        <span class="h3-extra">估计窗口与验证窗口严格分离 · 估计值为窗口内统计而非预测承诺</span>
        <span v-if="optData?.data_mode === 'REAL'" class="badge ok">真实数据</span>
      </h3>
      <div class="toolbar opt-toolbar">
        <div class="seg">
          <button
            v-for="method in OPT_METHODS"
            :key="method"
            type="button"
            :class="{ active: optMethod === method }"
            @click="optMethod = method"
          >{{ OPTIMIZATION_METHOD_LABELS[method] }}</button>
        </div>
        <label class="field"><span>估计窗口（日）</span><input v-model.number="optForm.estimation_window_days" type="number" min="60" step="21" /></label>
        <WlDateInput v-model="optForm.asof" label="决策时点 asof（缺省最新）" />
        <label class="field"><span>无风险利率</span><input v-model.number="optForm.risk_free_rate" type="number" step="0.005" /></label>
        <button class="mini primary" type="button" :disabled="optLoading" @click="runOptimization">{{ optLoading ? "优化中…" : "运行优化" }}</button>
      </div>
      <div v-if="optError" class="error-box">{{ optError }}</div>

      <div v-if="optData" class="opt-grid">
        <div ref="donutEl" class="donut"></div>
        <div class="opt-info">
          <div class="opt-metrics">
            <div v-for="item in optMetrics" :key="item.label" class="m-item">
              <span>{{ item.label }}</span>
              <b>{{ item.value }}</b>
            </div>
          </div>
          <dl class="opt-meta">
            <div><dt>估计窗口</dt><dd>{{ optData.estimation_window }}</dd></div>
            <div><dt>决策时点</dt><dd>{{ optData.asof }}</dd></div>
            <div><dt>适配器</dt><dd>{{ optData.adapter_used }}（pypfopt 可选，纯 numpy 兜底）</dd></div>
          </dl>
          <ul class="opt-notes">
            <li v-for="note in optData.notes" :key="note">{{ note }}</li>
          </ul>
        </div>
      </div>
      <div v-else-if="!optLoading" class="empty">选择优化方法后运行，生成长仓标准化目标权重。</div>
    </section>
  </div>
</template>

<style scoped>
.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.head-sub { margin: 0; }
.head-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chips-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.window-strip {
  display: flex; flex-wrap: wrap; align-items: center; gap: 18px;
  margin-bottom: 12px; padding: 10px 14px;
  border: 1px solid rgba(34, 211, 238, 0.35); border-radius: 10px;
  background: rgba(34, 211, 238, 0.05); font-size: 13px; color: var(--text);
  font-variant-numeric: tabular-nums;
}
.window-strip b { color: var(--text-dim); font-weight: 600; margin-right: 6px; }
.window-strip .dim { color: var(--text-dim); font-size: 12px; margin-left: auto; }
.compare-toolbar, .opt-toolbar { align-items: flex-end; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; color: var(--text-dim); }
.field input { min-width: 130px; }
button.primary { border-color: var(--accent); color: var(--accent); padding: 8px 16px; }
button.primary:hover:not(:disabled) { background: rgba(77, 163, 255, 0.12); }
.compare-table td.best { color: var(--gold); font-weight: 700; }
.compare-table th, .compare-table td { padding: 11px 12px; font-size: 13.5px; }
.chart-caption { display: flex; align-items: baseline; justify-content: space-between; margin-top: 10px; }
.chart-caption strong { font-size: 13px; }
.chart-caption span { font-size: 11px; color: var(--text-dim); }
.bar-chart { height: 260px; }
.opt-grid { display: grid; grid-template-columns: 360px 1fr; gap: 18px; margin-top: 8px; }
.donut { min-height: 300px; }
.opt-info { display: flex; flex-direction: column; gap: 12px; }
.opt-metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px 16px; border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; background: rgba(10, 14, 20, 0.5); }
.m-item { display: flex; flex-direction: column; gap: 2px; }
.m-item span { font-size: 11.5px; color: var(--text-dim); }
.m-item b { font-size: 16px; font-variant-numeric: tabular-nums; }
.opt-meta { margin: 0; display: flex; flex-direction: column; gap: 6px; }
.opt-meta div { display: flex; gap: 10px; font-size: 12.5px; }
.opt-meta dt { flex-shrink: 0; width: 76px; color: var(--text-dim); }
.opt-meta dd { margin: 0; color: var(--text); }
.opt-notes { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 5px; }
.opt-notes li { font-size: 11.5px; color: var(--text-dim); }
@media (max-width: 1280px) {
  .opt-grid { grid-template-columns: 1fr; }
}
</style>
