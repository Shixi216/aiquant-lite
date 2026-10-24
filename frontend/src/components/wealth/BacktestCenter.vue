<script setup lang="ts">
/**
 * 区域 C · 组合回测中心
 * 净值对比 / 历史回撤 / 指标卡片 / 滚动风险，全部直接消费契约响应：
 * series.portfolio_nav、series.benchmark_nav（EQUAL_WEIGHT 主基准）、
 * series.portfolio_drawdown、metrics、benchmarks、rebalances、data_quality。
 * 组件内不硬编码任何收益率；基准指标中 510300.SH 仅有 metrics（契约未提供其净值序列）。
 */
import { computed, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type { Asset, BacktestData, BacktestFormParams, Metrics, RiskData, WealthEnvelope } from "../../types/wealthLab";
import echarts, { type EChartsCoreOption } from "../../charts";
import { useECharts } from "../../composables/useECharts";
import { useChartTheme, withAlpha } from "../../domain/chartTheme";
import {
  REBALANCE_LABELS,
  assetName,
  fmtCny,
  fmtNav,
  fmtPct,
  fmtRatio,
  returnColorClass,
} from "../../domain/wealthFormat";
import WlDateInput from "../wealth/WlDateInput.vue";

const props = defineProps<{
  envelope: WealthEnvelope<BacktestData> | null;
  risk: WealthEnvelope<RiskData> | null;
  loading: boolean;
  error: string | null;
  assets: Asset[] | null;
}>();

const params = defineModel<BacktestFormParams>("params", { required: true });
const emit = defineEmits<{ run: [] }>();

const data = computed(() => props.envelope?.data ?? null);
const series = computed(() => data.value?.series ?? null);
const metrics = computed(() => data.value?.metrics ?? null);

const navEl = ref<HTMLElement | null>(null);
const drawdownEl = ref<HTMLElement | null>(null);
const rollingEl = ref<HTMLElement | null>(null);
const navChart = useECharts(navEl as Ref<HTMLElement | null>);
const drawdownChart = useECharts(drawdownEl as Ref<HTMLElement | null>);
const rollingChart = useECharts(rollingEl as Ref<HTMLElement | null>);

const ct = useChartTheme();
const tooltipBase = computed(() => ({
  backgroundColor: ct.value.tooltipBg,
  borderColor: ct.value.border,
  textStyle: { color: ct.value.tooltipText, fontSize: 12 } as const,
}));

const navOption = computed<EChartsCoreOption | null>(() => {
  const s = series.value;
  if (!s?.dates.length) return null;
  return {
    animationDuration: 700,
    tooltip: { trigger: "axis", ...tooltipBase.value, valueFormatter: (v: number) => fmtNav(v) },
    legend: { top: 0, right: 0, icon: "roundRect", itemWidth: 14, itemHeight: 4, textStyle: { color: ct.value.dim, fontSize: 12 } },
    grid: { left: 60, right: 74, top: 34, bottom: 62 },
    xAxis: {
      type: "category",
      data: s.dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: ct.value.axisLine } },
      axisLabel: { color: ct.value.dim, fontSize: 10.5 },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: ct.value.dim, fontSize: 10.5, formatter: (v: number) => v.toFixed(2) },
      splitLine: { lineStyle: { color: ct.value.grid } },
    },
    dataZoom: [
      { type: "inside" },
      { type: "slider", height: 18, bottom: 10, borderColor: ct.value.border, backgroundColor: ct.value.isDark ? "rgba(10,14,20,0.5)" : "rgba(255,255,255,0.6)", fillerColor: withAlpha(ct.value.accent, 0.15), handleStyle: { color: ct.value.accent }, textStyle: { color: ct.value.dim, fontSize: 10 } },
    ],
    series: [
      {
        name: "多资产组合",
        type: "line",
        data: s.portfolio_nav,
        showSymbol: false,
        lineStyle: { width: 2.6, color: ct.value.accent },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: withAlpha(ct.value.accent, 0.28) },
              { offset: 1, color: withAlpha(ct.value.accent, 0.02) },
            ],
          },
        },
        endLabel: { show: true, color: ct.value.accent, fontSize: 11, formatter: (p: { value: number }) => fmtNav(p.value) },
      },
      {
        name: "等权基准（EQUAL_WEIGHT）",
        type: "line",
        data: s.benchmark_nav,
        showSymbol: false,
        lineStyle: { width: 1.6, color: ct.value.dim, type: "dashed" },
      },
    ],
  } satisfies EChartsCoreOption;
});

const drawdownOption = computed<EChartsCoreOption | null>(() => {
  const s = series.value;
  if (!s?.dates.length) return null;
  return {
    animationDuration: 700,
    tooltip: { trigger: "axis", ...tooltipBase.value, valueFormatter: (v: number) => fmtPct(v) },
    grid: { left: 60, right: 20, top: 20, bottom: 26 },
    xAxis: {
      type: "category",
      data: s.dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: ct.value.axisLine } },
      axisLabel: { color: ct.value.dim, fontSize: 10.5 },
    },
    yAxis: {
      type: "value",
      max: 0,
      axisLabel: { color: ct.value.dim, fontSize: 10.5, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: ct.value.grid } },
    },
    series: [
      {
        name: "组合回撤",
        type: "line",
        data: s.portfolio_drawdown,
        showSymbol: false,
        lineStyle: { width: 2, color: ct.value.warn },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: withAlpha(ct.value.warn, 0.03) },
              { offset: 1, color: withAlpha(ct.value.warn, 0.3) },
            ],
          },
        },
      },
    ],
  } satisfies EChartsCoreOption;
});

const rollingOption = computed<EChartsCoreOption | null>(() => {
  const rolling = props.risk?.data.rolling;
  if (!rolling?.dates.length) return null;
  return {
    animationDuration: 700,
    tooltip: { trigger: "axis", ...tooltipBase.value },
    legend: { top: 0, right: 0, icon: "roundRect", itemWidth: 14, itemHeight: 4, textStyle: { color: ct.value.dim, fontSize: 12 } },
    grid: { left: 60, right: 56, top: 34, bottom: 26 },
    xAxis: {
      type: "category",
      data: rolling.dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: ct.value.axisLine } },
      axisLabel: { color: ct.value.dim, fontSize: 10.5 },
    },
    yAxis: [
      {
        type: "value",
        axisLabel: { color: ct.value.dim, fontSize: 10.5, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
        splitLine: { lineStyle: { color: ct.value.grid } },
      },
      {
        type: "value",
        axisLabel: { color: ct.value.dim, fontSize: 10.5, formatter: (v: number) => v.toFixed(1) },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "滚动波动率（63日）",
        type: "line",
        yAxisIndex: 0,
        data: rolling.volatility_63d,
        showSymbol: false,
        lineStyle: { width: 2, color: ct.value.accent2 },
      },
      {
        name: "滚动夏普（252日）",
        type: "line",
        yAxisIndex: 1,
        data: rolling.sharpe_252d,
        showSymbol: false,
        lineStyle: { width: 2, color: ct.value.gold },
      },
    ],
  } satisfies EChartsCoreOption;
});

// 空值守卫：数据未就绪（option 为 null）时不调用 setOption。
watchEffect(() => {
  const opt = navOption.value;
  if (opt) navChart.setOption(opt, true);
});
watchEffect(() => {
  const opt = drawdownOption.value;
  if (opt) drawdownChart.setOption(opt, true);
});
watchEffect(() => {
  const opt = rollingOption.value;
  if (opt) rollingChart.setOption(opt, true);
});

interface MetricCard {
  title: string;
  subtitle: string;
  metrics: Metrics | null;
  accent: boolean;
}

const metricCards = computed<MetricCard[]>(() => {
  const cards: MetricCard[] = [];
  if (metrics.value) {
    cards.push({ title: "多资产组合", subtitle: `${REBALANCE_LABELS[data.value?.params.rebalance ?? "monthly"] ?? ""} · 成本 ${data.value?.params.cost_bps ?? "—"} bps`, metrics: metrics.value, accent: true });
  }
  for (const benchmark of data.value?.benchmarks ?? []) {
    cards.push({
      title: benchmark.benchmark === "510300.SH" ? "基准 · 沪深300" : "基准 · 等权组合",
      subtitle: benchmark.benchmark === "510300.SH" ? "510300.SH" : "EQUAL_WEIGHT",
      metrics: benchmark.metrics,
      accent: false,
    });
  }
  return cards;
});

const recentRebalances = computed(() => (data.value?.rebalances ?? []).slice(-6).reverse());
const totalCost = computed(() => (data.value?.rebalances ?? []).reduce((acc, event) => acc + event.cost, 0));

function onCostChange(event: Event): void {
  const value = Number((event.target as HTMLInputElement).value);
  if (Number.isFinite(value) && value >= 0) {
    params.value = { ...params.value, cost_bps: value };
  }
}
</script>

<template>
  <section class="panel" aria-labelledby="wl-bt-title">
    <h3 id="wl-bt-title">
      组合回测中心
      <span class="h3-extra">月度再平衡 · 组合净值对比沪深300口径基准</span>
      <span v-if="envelope?.data.data_mode === 'DEMO'" class="badge warn">演示数据</span>
      <span v-else-if="envelope?.data.data_mode === 'REAL'" class="badge ok">真实数据</span>
    </h3>

      <div class="toolbar bt-toolbar">
        <WlDateInput :model-value="params.start_date" label="开始日期" @update:model-value="params = { ...params, start_date: $event }" />
        <WlDateInput :model-value="params.end_date" label="结束日期" @update:model-value="params = { ...params, end_date: $event }" />
      <label class="field">
        <span>调仓频率</span>
        <select v-model="params.rebalance">
          <option value="monthly">月度再平衡</option>
          <option value="quarterly">季度再平衡</option>
          <option value="none">不再平衡</option>
        </select>
      </label>
      <label class="field">
        <span>单边成本（bps）</span>
        <input type="number" min="0" step="1" :value="params.cost_bps" @change="onCostChange($event)" />
      </label>
      <span class="field-static">初始资金 {{ fmtCny(params.initial_capital) }}</span>
      <button class="mini primary" type="button" :disabled="loading" @click="emit('run')">{{ loading ? "回测中…" : "运行回测" }}</button>
      <span class="field-static dim">调仓 {{ data?.rebalances.length ?? "—" }} 次 · 累计成本 {{ fmtCny(totalCost, 2) }} · {{ data?.metrics.trading_days ?? "—" }} 个交易日</span>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>

    <template v-if="series?.dates.length">
      <div class="chart-caption">
        <strong>组合净值 vs 等权基准</strong>
        <span>数据源：series.portfolio_nav / series.benchmark_nav（契约 v1）</span>
      </div>
      <div ref="navEl" class="chart nav-chart"></div>

      <div class="bt-grid">
        <div>
          <div class="chart-caption"><strong>历史回撤</strong><span>series.portfolio_drawdown（负值区间）</span></div>
          <div ref="drawdownEl" class="chart dd-chart"></div>
        </div>
        <div>
          <div class="chart-caption"><strong>滚动风险指标</strong><span>risk.rolling（63日波动 / 252日夏普）</span></div>
          <div v-if="risk?.data.rolling.dates.length" ref="rollingEl" class="chart dd-chart"></div>
          <p v-else class="sub-empty">滚动风险序列待回测完成后加载。</p>
        </div>
      </div>

      <div class="metric-cards">
        <div v-for="card in metricCards" :key="card.title" class="m-card" :class="{ accent: card.accent }">
          <div class="m-head">
            <strong>{{ card.title }}</strong>
            <small>{{ card.subtitle }}</small>
          </div>
          <div class="m-body">
            <div class="m-item"><span>年化收益率</span><b :class="returnColorClass(card.metrics?.annual_return)">{{ fmtPct(card.metrics?.annual_return) }}</b></div>
            <div class="m-item"><span>年化波动率</span><b>{{ fmtPct(card.metrics?.annual_volatility, 2, false) }}</b></div>
            <div class="m-item"><span>最大回撤</span><b class="down">{{ fmtPct(card.metrics?.max_drawdown) }}</b></div>
            <div class="m-item"><span>夏普比率</span><b>{{ fmtRatio(card.metrics?.sharpe_ratio) }}</b></div>
          </div>
        </div>
      </div>

      <details v-if="recentRebalances.length" class="rebalance-details">
        <summary>调仓明细（最近 {{ recentRebalances.length }} 次 · 共 {{ data?.rebalances.length }} 次）</summary>
        <div class="table-scroll">
          <table class="data">
            <thead>
              <tr><th>调仓日期</th><th>资产</th><th class="num">调仓前权重</th><th class="num">调仓后权重</th><th class="num">单边换手</th><th class="num">本次成本</th></tr>
            </thead>
            <tbody>
              <template v-for="event in recentRebalances" :key="event.date">
                <tr v-for="(trade, index) in event.trades" :key="`${event.date}-${trade.symbol}`">
                  <td>{{ index === 0 ? event.date : "" }}</td>
                  <td>{{ assetName(assets, trade.symbol) }}<span class="sym-name">{{ trade.symbol }}</span></td>
                  <td class="num">{{ fmtPct(trade.weight_before, 2, false) }}</td>
                  <td class="num">{{ fmtPct(trade.weight_after, 2, false) }}</td>
                  <td class="num">{{ fmtPct(trade.turnover, 2, false) }}</td>
                  <td class="num">{{ index === 0 ? fmtCny(event.cost, 2) : "" }}</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </details>

      <p class="quality-line">
        {{ data?.data_quality.adjusted ? "后复权净值（fund_adj）" : "未复权净值" }}
        · 缺口 {{ data?.data_quality.missing_days ?? 0 }} 天
        <template v-for="caveat in data?.data_quality.caveats ?? []" :key="caveat"> · {{ caveat }}</template>
      </p>
    </template>
    <div v-else-if="!loading && !error" class="empty">暂无回测结果，请调整参数后运行回测。</div>
  </section>
</template>

<style scoped>
.bt-toolbar { align-items: flex-end; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; color: var(--text-dim); }
.field select, .field input { min-width: 130px; }
.field-static { font-size: 12px; color: var(--text); padding-bottom: 8px; font-variant-numeric: tabular-nums; }
.field-static.dim { color: var(--text-dim); }
button.primary { border-color: var(--accent); color: var(--accent); padding: 8px 16px; }
button.primary:hover:not(:disabled) { background: var(--accent-soft); }
.chart-caption { display: flex; align-items: baseline; justify-content: space-between; margin: 6px 0 2px; }
.chart-caption strong { font-size: 13px; }
.chart-caption span { font-size: 11px; color: var(--text-dim); }
.chart { width: 100%; }
.nav-chart { height: 360px; }
.dd-chart { height: 240px; }
.bt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 8px; }
.metric-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; margin-top: 14px; }
.m-card {
  border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px;
  background: linear-gradient(180deg, var(--bg-panel-2));
}
.m-card.accent { border-color: var(--accent-ring); box-shadow: inset 0 0 24px var(--accent-softer); }
.m-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 10px; }
.m-head strong { font-size: 13.5px; }
.m-head small { color: var(--text-dim); font-size: 11px; }
.m-body { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 14px; }
.m-item { display: flex; flex-direction: column; gap: 2px; }
.m-item span { font-size: 11.5px; color: var(--text-dim); }
.m-item b { font-size: 16px; font-variant-numeric: tabular-nums; }
.rebalance-details { margin-top: 12px; }
.rebalance-details summary { cursor: pointer; color: var(--text-dim); font-size: 12.5px; }
.rebalance-details summary:hover { color: var(--accent); }
.quality-line { margin: 12px 0 0; font-size: 11.5px; color: var(--text-dim); }
.sub-empty { color: var(--text-dim); font-size: 12.5px; padding: 24px 0; text-align: center; }
@media (max-width: 1100px) {
  .bt-grid { grid-template-columns: 1fr; }
}
</style>
