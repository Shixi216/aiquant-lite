<script setup lang="ts">
/**
 * 区域 B · 资产配置中心
 * 四资产权重调整（滑杆 + 数值输入）→ 环形图实时联动 → 权重总和校验 →
 * 有效变更后由父页面防抖触发重新回测；支持恢复契约默认配置（40/40/10/10）。
 */
import { computed, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type { Asset, WealthEnvelope, AssetsData } from "../../types/wealthLab";
import echarts, { type EChartsCoreOption } from "../../charts";
import { useECharts } from "../../composables/useECharts";
import {
  ASSET_CATEGORY_LABELS,
  ASSET_COLORS,
  DEFAULT_WEIGHTS,
  fmtCny,
  isWeightsValid,
  weightSum,
} from "../../domain/wealthFormat";
import { dataModeBadge } from "../../domain/wealthLabLabels";

const props = defineProps<{
  assets: WealthEnvelope<AssetsData> | null;
  assetsLoading: boolean;
  assetsError: string | null;
  initialCapital: number;
}>();

const weights = defineModel<Record<string, number>>("weights", { required: true });

const el = ref<HTMLElement | null>(null);
const { setOption } = useECharts(el as Ref<HTMLElement | null>);

// assets 接口不可用时的结构兜底（仅控件骨架，不虚构行情数据）。
const DEFAULT_ASSET_ROWS: Asset[] = [
  { symbol: "510300.SH", name: "沪深300ETF", category: "EQUITY_ETF", default_weight: 0.4, listing_date: "2012-05-28", data_first_date: null, data_last_date: null, data_rows: 0, data_status: "UNAVAILABLE" },
  { symbol: "511010.SH", name: "国债ETF", category: "BOND_ETF", default_weight: 0.4, listing_date: "2013-03-25", data_first_date: null, data_last_date: null, data_rows: 0, data_status: "UNAVAILABLE" },
  { symbol: "518880.SH", name: "黄金ETF", category: "GOLD_ETF", default_weight: 0.1, listing_date: "2013-07-29", data_first_date: null, data_last_date: null, data_rows: 0, data_status: "UNAVAILABLE" },
  { symbol: "511380.SH", name: "可转债ETF", category: "CONVERTIBLE_ETF", default_weight: 0.1, listing_date: "2020-03-12", data_first_date: null, data_last_date: null, data_rows: 0, data_status: "UNAVAILABLE" },
];

const assetRows = computed<Asset[]>(() => {
  const fromApi = props.assets?.data.assets;
  if (fromApi?.length) return fromApi;
  return DEFAULT_ASSET_ROWS;
});

const totalPct = computed(() => weightSum(weights.value) * 100);
const valid = computed(() => isWeightsValid(weights.value));
const sumText = computed(() => `${totalPct.value.toFixed(1)}%`);
const sumOff = computed(() => (totalPct.value - 100).toFixed(1));

function pctOf(symbol: string): number {
  return Math.round((weights.value[symbol] ?? 0) * 10000) / 100;
}

function setPct(symbol: string, pct: number): void {
  const clamped = Math.min(100, Math.max(0, Math.round(pct * 100) / 100));
  weights.value = { ...weights.value, [symbol]: clamped / 100 };
}

function resetDefaults(): void {
  const next: Record<string, number> = {};
  for (const asset of assetRows.value) {
    next[asset.symbol] = asset.default_weight;
  }
  weights.value = next;
}

const option = computed<EChartsCoreOption>(() => {
  const items = assetRows.value.map((asset) => ({
    name: asset.name,
    value: Math.round((weights.value[asset.symbol] ?? 0) * 10000) / 100,
    itemStyle: { color: ASSET_COLORS[asset.category] },
  }));
  const sumOk = valid.value;
  return {
    animationDuration: 500,
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(13,17,24,0.94)",
      borderColor: "#232c3b",
      textStyle: { color: "#dde4ee", fontSize: 12 },
      formatter: (p: { name: string; value: number; percent: number }) =>
        `${p.name}<br/>权重 ${p.value.toFixed(1)}% · 市值 ${fmtCny((props.initialCapital * p.value) / 100)}`,
    },
    legend: {
      bottom: 0,
      icon: "roundRect",
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: "#8595aa", fontSize: 11.5 },
    },
    title: {
      text: sumOk ? "100%" : sumText.value,
      subtext: sumOk ? "权重合计" : `合计偏差 ${sumOff.value} 个百分点`,
      left: "center",
      top: "34%",
      itemGap: 6,
      textStyle: { color: sumOk ? "#dde4ee" : "#f5a623", fontSize: 22, fontWeight: 700 },
      subtextStyle: { color: sumOk ? "#8595aa" : "#f5a623", fontSize: 11 },
    },
    series: [
      {
        name: "资产配置",
        type: "pie",
        radius: ["54%", "74%"],
        center: ["50%", "46%"],
        padAngle: 2,
        itemStyle: { borderColor: "#11161f", borderWidth: 2, borderRadius: 6 },
        label: { show: false },
        labelLine: { show: false },
        emphasis: { scaleSize: 6, label: { show: false } },
        data: items,
      },
    ],
  } satisfies EChartsCoreOption;
});

// 权重变更 → 环形图实时联动（watchEffect 同时覆盖首次挂载）。
watchEffect(() => {
  setOption(option.value, true);
});
</script>

<template>
  <section class="panel" aria-labelledby="wl-alloc-title">
    <h3 id="wl-alloc-title">
      资产配置中心
      <span class="h3-extra">第一版资产池 · 调整权重后自动重新回测</span>
      <span v-if="assetsLoading" class="badge">资产加载中…</span>
      <span v-else-if="assets" :class="dataModeBadge(assets.data.data_mode).cls">{{ dataModeBadge(assets.data.data_mode).text }}</span>    </h3>

    <div v-if="assetsError" class="error-box">{{ assetsError }}</div>

    <div class="alloc-grid">
      <div ref="el" class="donut"></div>

      <div class="controls">
        <div v-for="asset in assetRows" :key="asset.symbol" class="asset-row">
          <div class="asset-head">
            <span class="dot" :style="{ background: ASSET_COLORS[asset.category] }"></span>
            <div class="asset-name">
              <strong>{{ asset.name }}</strong>
              <small>{{ asset.symbol }} · {{ ASSET_CATEGORY_LABELS[asset.category] }}</small>
            </div>
            <input
              class="pct-input"
              type="number"
              min="0"
              max="100"
              step="1"
              :value="pctOf(asset.symbol)"
              :aria-label="`${asset.name} 权重百分比`"
              @change="setPct(asset.symbol, Number(($event.target as HTMLInputElement).value))"
            />
            <span class="pct-unit">%</span>
          </div>
          <input
            class="slider"
            type="range"
            min="0"
            max="100"
            step="1"
            :value="pctOf(asset.symbol)"
            :aria-label="`${asset.name} 权重滑杆`"
            :style="{ accentColor: ASSET_COLORS[asset.category] }"
            @input="setPct(asset.symbol, Number(($event.target as HTMLInputElement).value))"
          />
        </div>

        <div class="sum-line" :class="valid ? 'ok' : 'bad'">
          <span>权重合计：{{ sumText }}</span>
          <span v-if="!valid" class="sum-error">必须等于 100%（当前偏差 {{ sumOff }} 个百分点），已暂停回测</span>
          <span v-else class="sum-ok">校验通过 · 回测将按当前权重执行</span>
        </div>

        <div class="actions">
          <button class="mini primary" type="button" :disabled="!valid" @click="resetDefaults">恢复默认配置</button>
          <span class="hint">默认权重来自接口 <code>assets[].default_weight</code>（40 / 40 / 10 / 10）</span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.alloc-grid { display: grid; grid-template-columns: 340px 1fr; gap: 18px; align-items: stretch; }
.donut { min-height: 320px; }
.controls { display: flex; flex-direction: column; gap: 14px; justify-content: center; }
.asset-row { display: flex; flex-direction: column; gap: 6px; }
.asset-head { display: flex; align-items: center; gap: 10px; }
.dot { width: 9px; height: 9px; border-radius: 3px; flex-shrink: 0; }
.asset-name { display: flex; flex-direction: column; min-width: 0; }
.asset-name strong { font-size: 13.5px; }
.asset-name small { color: var(--text-dim); font-size: 11px; }
.pct-input { width: 84px; margin-left: auto; text-align: right; font-variant-numeric: tabular-nums; }
.pct-unit { color: var(--text-dim); font-size: 12px; width: 14px; }
.slider { width: 100%; height: 4px; cursor: pointer; }
.sum-line {
  display: flex; flex-direction: column; gap: 3px;
  padding: 10px 12px; border-radius: 10px; font-size: 12.5px;
  border: 1px solid var(--border); background: rgba(10, 14, 20, 0.5);
  font-variant-numeric: tabular-nums;
}
.sum-line.ok { border-color: rgba(0, 196, 140, 0.35); }
.sum-line.bad { border-color: var(--warn); }
.sum-error { color: var(--warn); font-size: 11.5px; }
.sum-ok { color: var(--down); font-size: 11.5px; }
.actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
button.primary { border-color: var(--accent); color: var(--accent); }
button.primary:hover:not(:disabled) { background: rgba(77, 163, 255, 0.12); }
.hint { color: var(--text-dim); font-size: 11.5px; }
.hint code { color: var(--accent-2); font-size: 11px; }
@media (max-width: 1100px) {
  .alloc-grid { grid-template-columns: 1fr; }
  .donut { min-height: 280px; }
}
</style>
