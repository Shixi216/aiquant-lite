<script setup lang="ts">
/**
 * 区域 D · 风险压力测试
 * 合成冲击（权益/债券/黄金/多资产同跌，冲击幅度可编辑）与历史窗口情景（2020-03 疫情冲击）。
 * 全部通过 POST /v1/wealth-lab/stress-tests 执行；风险偏好为演示选项，
 * 阈值仅为研究/演示规则，不构成任何正式银行适当性判断。
 */
import { computed, onMounted, ref, watch, watchEffect } from "vue";
import type { Ref } from "vue";
import type { Asset, AssetContribution, StressTestData, WealthEnvelope } from "../../types/wealthLab";
import type { DataSource } from "../../api/wealthLab";
import { runWealthStressTest } from "../../api/wealthLab";
import { errorMessage } from "../../api/client";
import type { EChartsCoreOption } from "../../charts";
import { useECharts } from "../../composables/useECharts";
import { useChartTheme } from "../../domain/chartTheme";
import { ASSET_COLORS, fmtCny, fmtPct, isWeightsValid } from "../../domain/wealthFormat";
import { DOWN_COLOR, UP_COLOR } from "../../domain/labels";

const props = defineProps<{
  weights: Record<string, number>;
  initialCapital: number;
  assets: Asset[] | null;
}>();

const emit = defineEmits<{ applyProfile: [weights: Record<string, number>] }>();

function applyProfile(weights: Record<string, number>): void {
  emit("applyProfile", weights);
}

interface SyntheticScenario {
  key: string;
  kind: "synthetic";
  label: string;
  desc: string;
  shocks: Record<string, number>;
}

interface HistoricalScenario {
  key: string;
  kind: "historical";
  label: string;
  desc: string;
  start_date: string;
  end_date: string;
}

type ScenarioDef = SyntheticScenario | HistoricalScenario;

const SYMBOLS = ["510300.SH", "511010.SH", "518880.SH", "511380.SH"];

/** 演示冲击假设（可编辑），仅用于展示压力测试机制。 */
const SCENARIOS: ScenarioDef[] = [
  { key: "equity", kind: "synthetic", label: "权益资产下跌", desc: "沪深300 单日大幅下挫，考验权益仓位冲击", shocks: { "510300.SH": -0.07, "511010.SH": 0, "518880.SH": 0, "511380.SH": 0 } },
  { key: "bond", kind: "synthetic", label: "债券资产下跌", desc: "利率上行冲击债券 ETF，考察防御垫厚度", shocks: { "510300.SH": 0, "511010.SH": -0.02, "518880.SH": 0, "511380.SH": 0 } },
  { key: "gold", kind: "synthetic", label: "黄金资产波动", desc: "金价单日剧烈波动，考察避险资产的双向性", shocks: { "510300.SH": 0, "511010.SH": 0, "518880.SH": -0.05, "511380.SH": 0 } },
  { key: "multi", kind: "synthetic", label: "多资产同跌", desc: "股债金转债同时下跌（对应契约合成样例）", shocks: { "510300.SH": -0.1, "511010.SH": -0.02, "518880.SH": -0.08, "511380.SH": -0.02 } },
  { key: "covid", kind: "historical", label: "2020-03 疫情冲击", desc: "历史窗口：2020-03-05 ~ 2020-03-31 真实行情", start_date: "2020-03-05", end_date: "2020-03-31" },
];

/** 风险偏好演示选项：仅调整权重的研究规则，不是适当性评级。 */
const RISK_PROFILES = [
  { key: "conservative", label: "保守型（演示）", weights: { "510300.SH": 0.2, "511010.SH": 0.6, "518880.SH": 0.1, "511380.SH": 0.1 } },
  { key: "balanced", label: "平衡型（演示）", weights: { "510300.SH": 0.4, "511010.SH": 0.4, "518880.SH": 0.1, "511380.SH": 0.1 } },
  { key: "aggressive", label: "进取型（演示）", weights: { "510300.SH": 0.6, "511010.SH": 0.2, "518880.SH": 0.1, "511380.SH": 0.1 } },
];

const activeKey = ref("multi");
const shockEdits = ref<Record<string, Record<string, number>>>(
  Object.fromEntries(
    SCENARIOS.filter((s): s is SyntheticScenario => s.kind === "synthetic").map((s) => [s.key, { ...s.shocks }]),
  ),
);
const result = ref<WealthEnvelope<StressTestData> | null>(null);
const source = ref<DataSource>("live");
const loading = ref(false);
const error = ref<string | null>(null);
const appliedLabel = ref("");

const activeScenario = computed(() => SCENARIOS.find((s) => s.key === activeKey.value) ?? SCENARIOS[0]!);
const weightsValid = computed(() => isWeightsValid(props.weights));

const data = computed(() => result.value?.data ?? null);
const shockReturn = computed(() => data.value?.portfolio_shock_return ?? null);
const lossAmount = computed(() =>
  shockReturn.value !== null ? props.initialCapital * shockReturn.value : null,
);

const lossLevel = computed(() => {
  const loss = shockReturn.value;
  if (loss === null) return null;
  const magnitude = Math.abs(loss);
  // 演示阈值：仅用于页面警示样式，不是正式适当性规则。
  if (magnitude >= 0.1) return { text: "极端压力（演示阈值）", cls: "badge veto" };
  if (magnitude >= 0.06) return { text: "严重压力（演示阈值）", cls: "badge warn" };
  if (magnitude >= 0.03) return { text: "中度压力（演示阈值）", cls: "badge warn" };
  return { text: "轻度压力（演示阈值）", cls: "badge ok" };
});

function assetName(symbol: string): string {
  return props.assets?.find((asset) => asset.symbol === symbol)?.name ?? symbol;
}

function assetColor(symbol: string): string {
  const category = props.assets?.find((asset) => asset.symbol === symbol)?.category;
  return category ? ASSET_COLORS[category] : ct.value.dim;
}

const el = ref<HTMLElement | null>(null);
const { setOption } = useECharts(el as Ref<HTMLElement | null>);
const ct = useChartTheme();

const contributionOption = computed<EChartsCoreOption | null>(() => {
  const items: AssetContribution[] = data.value?.asset_contributions ?? [];
  if (!items.length) return null;
  const sorted = [...items].sort((a, b) => a.contribution - b.contribution);
  return {
    animationDuration: 500,
    tooltip: {
      trigger: "item",
      backgroundColor: ct.value.tooltipBg,
      borderColor: ct.value.border,
      textStyle: { color: ct.value.tooltipText, fontSize: 12 },
      formatter: (p: { dataIndex: number }) => {
        const item = sorted[p.dataIndex]!;
        return `${assetName(item.symbol)}<br/>权重 ${fmtPct(item.weight, 1, false)} · 单日冲击 ${fmtPct(item.shock_return)}<br/>组合贡献 ${fmtPct(item.contribution)}（${fmtCny(props.initialCapital * item.contribution, 2)}）`;
      },
    },
    grid: { left: 88, right: 48, top: 10, bottom: 24 },
    xAxis: {
      type: "value",
      axisLabel: { color: ct.value.dim, fontSize: 10.5, formatter: (v: number) => `${(v * 100).toFixed(1)}%` },
      splitLine: { lineStyle: { color: ct.value.grid } },
    },
    yAxis: {
      type: "category",
      data: sorted.map((item) => assetName(item.symbol)),
      axisLabel: { color: ct.value.text, fontSize: 11.5 },
      axisLine: { lineStyle: { color: ct.value.axisLine } },
    },
    series: [
      {
        name: "损失贡献",
        type: "bar",
        barWidth: 16,
        itemStyle: { borderRadius: 4 },
        data: sorted.map((item) => ({
          value: item.contribution,
          itemStyle: { color: item.contribution <= 0 ? DOWN_COLOR : UP_COLOR },
        })),
        label: {
          show: true,
          position: "right",
          color: ct.value.dim,
          fontSize: 10.5,
          formatter: (p: { value: number }) => fmtPct(p.value),
        },
      },
    ],
  } satisfies EChartsCoreOption;
});

let debounceTimer: ReturnType<typeof setTimeout> | undefined;
let requestSeq = 0;
/** 串行化：DuckDB 单写者后端对并发 POST 敏感，避免 stress 与回测请求互相踩踏。 */
let running = false;
let pendingRun = false;

async function run(): Promise<void> {
  if (running) {
    pendingRun = true;
    return;
  }
  const scenario = activeScenario.value;
  if (!weightsValid.value) {
    error.value = "当前权重合计不等于 100%，请先在资产配置中心修正后再运行压力测试。";
    return;
  }
  const seq = ++requestSeq;
  running = true;
  loading.value = true;
  error.value = null;
  try {
    const payload =
      scenario.kind === "historical"
        ? { scenario: { kind: "historical" as const, label: scenario.label, start_date: scenario.start_date, end_date: scenario.end_date }, weights: { ...props.weights } }
        : { scenario: { kind: "synthetic" as const, label: scenario.label, shocks: { ...shockEdits.value[scenario.key] } }, weights: { ...props.weights } };
    const response = await runWealthStressTest(payload);
    if (seq !== requestSeq) return;
    result.value = response.envelope;
    source.value = response.source;
    appliedLabel.value = response.envelope.data.scenario_label;
    error.value = null;
  } catch (err) {
    if (seq === requestSeq) {
      // 后端偶发繁忙时静默重试一次；仅连续失败才呈现错误（保留上一次成功结果）。
      await new Promise((resolve) => setTimeout(resolve, 700));
      if (seq === requestSeq && running && !pendingRun) {
        try {
          const retry =
            scenario.kind === "historical"
              ? { scenario: { kind: "historical" as const, label: scenario.label, start_date: scenario.start_date, end_date: scenario.end_date }, weights: { ...props.weights } }
              : { scenario: { kind: "synthetic" as const, label: scenario.label, shocks: { ...shockEdits.value[scenario.key] } }, weights: { ...props.weights } };
          const response = await runWealthStressTest(retry);
          if (seq === requestSeq) {
            result.value = response.envelope;
            source.value = response.source;
            appliedLabel.value = response.envelope.data.scenario_label;
            error.value = null;
          }
        } catch (retryErr) {
          if (seq === requestSeq) error.value = errorMessage(retryErr);
        }
      }
    }
  } finally {
    running = false;
    if (seq === requestSeq) loading.value = false;
    if (pendingRun) {
      pendingRun = false;
      void run();
    }
  }
}

function schedule(): void {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(run, 600);
}

watch([activeKey, shockEdits, () => props.weights], schedule, { deep: true });

// 空值守卫：数据未就绪（option 为 null）时不调用 setOption，防止 ECharts 报 option null。
watchEffect(() => {
  const opt = contributionOption.value;
  if (opt) setOption(opt, true);
});

onMounted(run);
</script>

<template>
  <section class="panel" aria-labelledby="wl-stress-title">
    <h3 id="wl-stress-title">
      风险压力测试
      <span class="h3-extra">冲击情景损失分析 · 研究演示规则，非正式适当性判断</span>
      <span v-if="data?.data_mode === 'DEMO' || source === 'offline-mock'" class="badge warn">演示口径</span>
      <span v-else-if="data?.data_mode === 'REAL'" class="badge ok">真实数据</span>
    </h3>

    <div class="stress-grid">
      <div class="stress-controls">
        <div class="seg scenario-seg" role="tablist" aria-label="压力情景">
          <button
            v-for="scenario in SCENARIOS"
            :key="scenario.key"
            type="button"
            role="tab"
              :aria-selected="activeKey === scenario.key"
              :class="{ active: activeKey === scenario.key }"
              @click="activeKey = scenario.key"
            >{{ scenario.label }}</button>
        </div>
        <p class="scenario-desc">{{ activeScenario.desc }}</p>

        <template v-if="activeScenario.kind === 'synthetic'">
          <div class="shock-list">
            <label v-for="symbol in SYMBOLS" :key="symbol" class="shock-row">
              <span class="shock-name"><i :style="{ background: assetColor(symbol) }"></i>{{ assetName(symbol) }}</span>
              <input
                type="number"
                min="-30"
                max="10"
                step="0.5"
                :value="Math.round((shockEdits[activeScenario.key]?.[symbol] ?? 0) * 10000) / 100"
                :aria-label="`${assetName(symbol)} 单日冲击收益率（百分比）`"
                @change="shockEdits[activeScenario.key]![symbol] = Number(($event.target as HTMLInputElement).value) / 100"
              />
              <span class="pct-unit">%</span>
            </label>
          </div>
          <p class="assumption">输入假设：单日冲击收益率（小数），0 表示该资产不受冲击；可编辑后自动重算。</p>
        </template>
        <p v-else class="assumption">历史窗口：{{ activeScenario.start_date }} ~ {{ activeScenario.end_date }}，由后端按真实行情重放计算。</p>

        <div class="profile-box">
          <span class="profile-title">风险偏好演示选项（研究规则，非银行适当性判断）</span>
          <div class="profile-chips">
            <button
              v-for="profile in RISK_PROFILES"
              :key="profile.key"
              type="button"
              class="chip"
              @click="applyProfile(profile.weights)"
            >{{ profile.label }}</button>
          </div>
          <p class="assumption">点击后同步调整「资产配置中心」权重，并联动重跑回测与本压力测试。</p>
        </div>

        <div class="assumption-box">
          <strong>计算假设与口径</strong>
          <ul>
            <li>损失金额 = 回测初始资金（{{ fmtCny(initialCapital) }}）× 组合冲击收益率。</li>
            <li>各资产损失贡献 = 资产权重 × 该资产单日冲击收益率（线性近似，不含相关性放大）。</li>
            <li>压力等级阈值为研究演示规则（3% / 6% / 10%），不构成任何正式适当性结论。</li>
            <li>历史情景结果取决于后端数据池覆盖范围；合成情景为演示假设。</li>
          </ul>
        </div>
      </div>

      <div class="stress-results">
        <div v-if="error" class="error-box">{{ error }}</div>

        <template v-if="data">
          <div class="loss-row">
            <div class="loss-card">
              <span class="loss-label">组合损失率 · {{ appliedLabel }}</span>
              <span class="loss-value down">{{ fmtPct(shockReturn) }}</span>
              <span :class="lossLevel?.cls">{{ lossLevel?.text }}</span>
            </div>
            <div class="loss-card">
              <span class="loss-label">估计损失金额</span>
              <span class="loss-value down">{{ fmtCny(lossAmount, 2) }}</span>
              <span class="loss-foot">按初始资金 {{ fmtCny(initialCapital) }} 线性折算</span>
            </div>
          </div>

          <div class="chart-caption"><strong>各资产损失贡献</strong><span>weight × shock_return（正值为对冲贡献）</span></div>
          <div ref="el" class="stress-chart"></div>

          <div class="table-scroll contrib-table">
            <table class="data">
              <thead>
                <tr><th>资产</th><th class="num">权重</th><th class="num">单日冲击</th><th class="num">组合贡献</th><th class="num">贡献金额</th></tr>
              </thead>
              <tbody>
                <tr v-for="item in data.asset_contributions" :key="item.symbol">
                  <td><i class="dot" :style="{ background: assetColor(item.symbol) }"></i>{{ assetName(item.symbol) }}<span class="sym-name">{{ item.symbol }}</span></td>
                  <td class="num">{{ fmtPct(item.weight, 1, false) }}</td>
                  <td class="num" :class="item.shock_return > 0 ? 'up' : 'down'">{{ fmtPct(item.shock_return) }}</td>
                  <td class="num" :class="item.contribution > 0 ? 'up' : 'down'">{{ fmtPct(item.contribution) }}</td>
                  <td class="num">{{ fmtCny(initialCapital * item.contribution, 2) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="risk-warning" role="alert">
            <strong>⚠ 风险警示</strong>
            <p v-if="shockReturn !== null && shockReturn < -0.06">
              该情景下组合单日损失超过 6%（演示阈值）：权益与避险资产相关性可能同时失效，建议检查组合压力承受能力与再平衡预案。
            </p>
            <p v-else-if="shockReturn !== null && shockReturn < -0.03">
              该情景下组合单日损失介于 3%~6%（演示阈值）：处于历史极端波动区间，关注权益仓位与流动性储备。
            </p>
            <p v-else>
              该情景下组合单日损失低于 3%（演示阈值）：防御资产对冲有效，但仍需关注多资产相关性在极端行情下抬升的风险。
            </p>
            <p class="disclaimer">以上阈值与等级均为研究/演示规则，不能作为正式银行适当性判断或投资建议。</p>
          </div>
        </template>
        <div v-else-if="!loading && !error" class="empty">暂无压力测试结果。</div>
        <div v-if="loading" class="loading">压力测试计算中…</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stress-grid { display: grid; grid-template-columns: 380px 1fr; gap: 18px; }
.stress-controls { display: flex; flex-direction: column; gap: 12px; }
.scenario-seg { flex-direction: column; align-items: stretch; }
.scenario-seg button { text-align: left; }
.scenario-desc { margin: 0; font-size: 12px; color: var(--text-dim); }
.shock-list { display: flex; flex-direction: column; gap: 8px; }
.shock-row { display: flex; align-items: center; gap: 10px; font-size: 12.5px; }
.shock-name { display: flex; align-items: center; gap: 8px; width: 110px; color: var(--text); }
.shock-name i { width: 8px; height: 8px; border-radius: 3px; display: inline-block; }
.shock-row input { width: 90px; text-align: right; font-variant-numeric: tabular-nums; }
.pct-unit { color: var(--text-dim); font-size: 12px; }
.assumption { margin: 0; font-size: 11.5px; color: var(--text-dim); }
.profile-box { border: 1px dashed color-mix(in srgb, var(--warn) 45%, transparent); border-radius: 10px; padding: 10px 12px; }
.profile-title { display: block; font-size: 12px; color: var(--warn); margin-bottom: 8px; }
.profile-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.assumption-box { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--bg-hover); }
.assumption-box strong { font-size: 12.5px; }
.assumption-box ul { margin: 8px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 5px; }
.assumption-box li { font-size: 11.5px; color: var(--text-dim); }
.stress-results { display: flex; flex-direction: column; gap: 12px; }
.loss-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.loss-card {
  display: flex; flex-direction: column; gap: 5px;
  border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px;
  background: linear-gradient(180deg, var(--bg-panel-2));
}
.loss-label { font-size: 12px; color: var(--text-dim); }
.loss-value { font-size: 30px; font-weight: 700; font-variant-numeric: tabular-nums; }
.loss-foot { font-size: 11px; color: var(--text-dim); }
.chart-caption { display: flex; align-items: baseline; justify-content: space-between; }
.chart-caption strong { font-size: 13px; }
.chart-caption span { font-size: 11px; color: var(--text-dim); }
.stress-chart { height: 200px; }
.contrib-table { max-height: none; }
.dot { width: 8px; height: 8px; border-radius: 3px; display: inline-block; margin-right: 8px; }
.risk-warning {
  border: 1px solid color-mix(in srgb, var(--warn) 50%, transparent);
  background: color-mix(in srgb, var(--warn) 8%, transparent);
  border-radius: 10px;
  padding: 12px 14px;
}
.risk-warning strong { color: var(--warn); font-size: 13px; }
.risk-warning p { margin: 8px 0 0; font-size: 12.5px; color: var(--text); }
.risk-warning .disclaimer { color: var(--text-dim); font-size: 11px; }
@media (max-width: 1280px) {
  .stress-grid { grid-template-columns: 1fr; }
  .loss-row { grid-template-columns: 1fr; }
}
</style>
