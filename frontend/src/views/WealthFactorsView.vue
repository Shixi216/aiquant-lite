<script setup lang="ts">
/**
 * 中国 ETF 多因子研究（v2）：动量/趋势/波动/回撤/流动性五因子评分与综合排序。
 * GET /v1/wealth-lab/v2/factors（可指定 asof）+ /v2/universe 研究池审计。
 * 中文标签统一取自 domain/wealthLabLabels.ts；综合指标按数据实态动态命名（分位 or 得分）。
 */
import { computed, onMounted, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type { FactorsData, UniverseData, WealthEnvelope } from "../types/wealthLabV2";
import { fetchV2Factors, fetchV2Universe } from "../api/wealthLabV2";
import type { DataSource } from "../api/wealthLab";
import { errorMessage } from "../api/client";
import echarts, { type EChartsCoreOption } from "../charts";
import { useECharts } from "../composables/useECharts";
import { fmtCny, fmtPct, fmtRatio, returnColorClass } from "../domain/wealthFormat";
import { V2_CATEGORY_LABELS } from "../domain/wealthV2Format";
import { FACTOR_HINTS, FACTOR_LABELS, compositeMetric } from "../domain/wealthLabLabels";

const factorsEnvelope = ref<WealthEnvelope<FactorsData> | null>(null);
const universeEnvelope = ref<WealthEnvelope<UniverseData> | null>(null);
const source = ref<DataSource>("live");
const loading = ref(false);
const error = ref<string | null>(null);

const factors = computed(() => factorsEnvelope.value?.data.factors ?? []);
const universe = computed(() => universeEnvelope.value?.data.assets ?? []);
const asof = computed(() => factorsEnvelope.value?.data.asof ?? "");

/** 综合指标按数据实态命名：全 [0,1] → 综合分位；出现负值 → 综合因子得分（不改数值）。 */
const composite = computed(() => compositeMetric(factors.value.map((row) => row.composite_rank)));

function assetName(symbol: string): string {
  return universe.value.find((asset) => asset.symbol === symbol)?.name ?? symbol;
}

function assetCategory(symbol: string): string {
  const category = universe.value.find((asset) => asset.symbol === symbol)?.category;
  return category ? V2_CATEGORY_LABELS[category] : "";
}

const rankEl = ref<HTMLElement | null>(null);
const rankChart = useECharts(rankEl as Ref<HTMLElement | null>);

const rankOption = computed<EChartsCoreOption | null>(() => {
  const rows = [...factors.value].sort((a, b) => a.composite_rank - b.composite_rank);
  if (!rows.length) return null;
  return {
    animationDuration: 500,
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(13,17,24,0.95)",
      borderColor: "#232c3b",
      textStyle: { color: "#dde4ee", fontSize: 13 },
      formatter: (p: { dataIndex: number }) => {
        const row = rows[p.dataIndex]!;
        return `<b>${assetName(row.symbol)}</b>（${row.symbol}）<br/>${composite.value.label}：<b>${fmtRatio(row.composite_rank, 2)}</b><br/>${FACTOR_LABELS.momentum_126d}：${fmtPct(row.momentum_126d)}<br/>${FACTOR_LABELS.trend_score}：${fmtPct(row.trend_score)}<br/>${FACTOR_LABELS.volatility_63d}：${fmtPct(row.volatility_63d, 2, false)}<br/>${FACTOR_LABELS.max_drawdown_252d}：${fmtPct(row.max_drawdown_252d)}`;
      },
    },
    grid: { left: 168, right: 64, top: 14, bottom: 28 },
    xAxis: {
      type: "value",
      axisLabel: { color: "#8595aa", fontSize: 12, formatter: (v: number) => v.toFixed(1) },
      splitLine: { lineStyle: { color: "rgba(38,49,64,0.5)" } },
    },
    yAxis: {
      type: "category",
      data: rows.map((row) => `${assetName(row.symbol)} ${row.symbol}`),
      axisLabel: { color: "#dde4ee", fontSize: 12.5 },
      axisLine: { lineStyle: { color: "#232c3b" } },
    },
    series: [
      {
        name: composite.value.label,
        type: "bar",
        barWidth: 20,
        itemStyle: { color: "#4da3ff", borderRadius: 4 },
        data: rows.map((row) => row.composite_rank),
        label: {
          show: true,
          position: "right",
          color: "#dde4ee",
          fontSize: 12,
          formatter: (p: { value: number }) => fmtRatio(p.value, 2),
        },
      },
    ],
  } satisfies EChartsCoreOption;
});

watchEffect(() => {
  const opt = rankOption.value;
  if (opt) rankChart.setOption(opt, true);
});

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [factorsResult, universeResult] = await Promise.all([fetchV2Factors(), fetchV2Universe()]);
    factorsEnvelope.value = factorsResult.envelope;
    universeEnvelope.value = universeResult.envelope;
    source.value = factorsResult.source;
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="factor-research">
    <div class="page-head">
      <p class="page-sub head-sub">五因子截面（动量 / 趋势 / 波动 / 回撤 / 流动性）· 仅使用截至数据日的历史窗口，不构成投资建议</p>
      <div class="head-chips">
        <span v-if="source === 'offline-mock'" class="badge warn">契约 Mock 样例</span>
        <span v-else-if="factorsEnvelope?.data.data_mode === 'REAL'" class="badge ok">真实数据</span>
        <span v-else-if="factorsEnvelope" class="badge warn">演示数据</span>
        <span v-if="asof" class="badge action">数据截至 {{ asof }}</span>
        <button class="mini" @click="load">{{ loading ? "刷新中…" : "刷新" }}</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>

    <section class="panel" aria-labelledby="wl2-factor-title">
      <h3 id="wl2-factor-title">
        因子评分与综合排序
        <span class="h3-extra">{{ composite.hint }}</span>
      </h3>
      <div class="factor-grid">
        <div ref="rankEl" class="rank-chart"></div>
        <div class="table-scroll factor-table">
          <table class="data factor-data">
            <thead>
              <tr>
                <th>资产</th>
                <th class="num" :title="FACTOR_HINTS.momentum_126d">{{ FACTOR_LABELS.momentum_126d }}</th>
                <th class="num" :title="FACTOR_HINTS.trend_score">{{ FACTOR_LABELS.trend_score }}</th>
                <th class="num" :title="FACTOR_HINTS.volatility_63d">{{ FACTOR_LABELS.volatility_63d }}</th>
                <th class="num" :title="FACTOR_HINTS.max_drawdown_252d">{{ FACTOR_LABELS.max_drawdown_252d }}</th>
                <th class="num" :title="FACTOR_HINTS.liquidity_20d_avg_amount_cny">{{ FACTOR_LABELS.liquidity_20d_avg_amount_cny }}</th>
                <th class="num" :title="composite.hint">{{ composite.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in factors" :key="row.symbol">
                <td>
                  <strong>{{ assetName(row.symbol) }}</strong>
                  <span class="sym-name">{{ row.symbol }} · {{ assetCategory(row.symbol) }}</span>
                </td>
                <td class="num" :class="returnColorClass(row.momentum_126d)">{{ fmtPct(row.momentum_126d) }}</td>
                <td class="num" :class="returnColorClass(row.trend_score)">{{ fmtPct(row.trend_score) }}</td>
                <td class="num">{{ fmtPct(row.volatility_63d, 2, false) }}</td>
                <td class="num down">{{ fmtPct(row.max_drawdown_252d) }}</td>
                <td class="num">{{ fmtCny(row.liquidity_20d_avg_amount_cny) }}</td>
                <td class="num gold-text"><strong>{{ fmtRatio(row.composite_rank, 2) }}</strong></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="panel" aria-labelledby="wl2-universe-title">
      <h3 id="wl2-universe-title">
        多资产研究池
        <span class="h3-extra">中国市场核心池（上交所/深交所 · 人民币），逐步扩容；数据来源逐资产审计</span>
      </h3>
      <div class="table-scroll">
        <table class="data factor-data">
          <thead>
            <tr>
              <th>资产</th><th>类别</th><th>交易所</th><th>上市日期</th>
              <th class="num">数据区间</th><th class="num">行情行数</th><th>数据状态</th><th>来源构成</th><th>风险备注</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="asset in universe" :key="asset.symbol">
              <td><strong>{{ asset.name }}</strong><span class="sym-name">{{ asset.symbol }}</span></td>
              <td>{{ V2_CATEGORY_LABELS[asset.category] }}</td>
              <td>{{ asset.exchange === "SSE" ? "上交所" : "深交所" }}</td>
              <td>{{ asset.listing_date }}</td>
              <td class="num">{{ asset.data_first_date }} ~ {{ asset.data_last_date }}</td>
              <td class="num">{{ asset.data_rows.toLocaleString("zh-CN") }}</td>
              <td><span :class="asset.data_status === 'REAL' ? 'badge ok' : 'badge warn'">{{ asset.data_status === "REAL" ? "真实数据" : "演示数据" }}</span></td>
              <td>
                <span v-for="(count, key) in asset.source_breakdown" :key="key" class="src-chip">{{ key }}：{{ count.toLocaleString("zh-CN") }} 行</span>
              </td>
              <td class="dim-cell">{{ asset.risk_notes }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.head-sub { margin: 0; font-size: 13px; }
.head-chips { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.factor-grid { display: grid; grid-template-columns: 430px 1fr; gap: 18px; align-items: start; }
.rank-chart { height: 300px; }
.factor-table { max-height: 320px; }
.factor-data th, .factor-data td { padding: 11px 12px; font-size: 13.5px; }
.src-chip { display: inline-block; margin: 1px 4px 1px 0; padding: 2px 8px; border: 1px solid var(--border); border-radius: 8px; font-size: 11.5px; color: var(--text-dim); font-variant-numeric: tabular-nums; }
.dim-cell { color: var(--text-dim); font-size: 12.5px; }
@media (max-width: 1280px) {
  .factor-grid { grid-template-columns: 1fr; }
}
</style>
