<script setup lang="ts">
/**
 * 财富管理 / 大类资产配置（AIQUANT-LITE 财富管理实验室）
 * 契约：docs/contracts/wealth-lab-api-v1.yaml（v1 已冻结）。
 * 页面只做编排：数据加载、权重状态、防抖回测、模式标注；HTTP 全部经 api/wealthLab.ts。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import type {
  AssetsData,
  BacktestData,
  MetaData,
  RiskData,
  WealthEnvelope,
} from "../types/wealthLab";
import type { BacktestFormParams } from "../types/wealthLab";
import type { DataSource } from "../api/wealthLab";
import { fetchWealthAssets, fetchWealthMeta, fetchWealthRisk, runWealthBacktest } from "../api/wealthLab";
import { errorMessage } from "../api/client";
import WealthDashboard from "../components/wealth/WealthDashboard.vue";
import AllocationCenter from "../components/wealth/AllocationCenter.vue";
import BacktestCenter from "../components/wealth/BacktestCenter.vue";
import StressTestCenter from "../components/wealth/StressTestCenter.vue";
import { DEFAULT_WEIGHTS, isWeightsValid } from "../domain/wealthFormat";

/* ---------------- 基础数据（meta + assets） ---------------- */

const meta = ref<WealthEnvelope<MetaData> | null>(null);
const assets = ref<WealthEnvelope<AssetsData> | null>(null);
const assetsLoading = ref(false);
const assetsError = ref<string | null>(null);
const baseSource = ref<DataSource>("live");
let baseInitialized = false;

/* ---------------- 权重与回测参数 ---------------- */

const weights = ref<Record<string, number>>({ ...DEFAULT_WEIGHTS });
const backtestParams = reactive<BacktestFormParams>({
  start_date: "2020-04-08", // 契约 BacktestRequest 示例区间；assets 可用后改用四资产共同覆盖起点
  end_date: "2026-10-23",
  rebalance: "monthly",
  cost_bps: 5,
  initial_capital: 1000000,
});

const weightsValid = computed(() => isWeightsValid(weights.value));

/* ---------------- 回测与风险 ---------------- */

const backtest = ref<WealthEnvelope<BacktestData> | null>(null);
const backtestSource = ref<DataSource>("live");
const backtestLoading = ref(false);
const backtestError = ref<string | null>(null);
const risk = ref<WealthEnvelope<RiskData> | null>(null);
let backtestSeq = 0;
let debounceTimer: ReturnType<typeof setTimeout> | undefined;

const overallSource = computed<DataSource>(() =>
  baseSource.value === "offline-mock" || backtestSource.value === "offline-mock" ? "offline-mock" : "live",
);

const offlineMode = computed(() => overallSource.value === "offline-mock");
const demoMode = computed(() => backtest.value?.data.data_mode === "DEMO");

async function loadBase(): Promise<void> {
  assetsLoading.value = true;
  assetsError.value = null;
  try {
    const [metaResult, assetsResult] = await Promise.all([fetchWealthMeta(), fetchWealthAssets()]);
    meta.value = metaResult.envelope;
    assets.value = assetsResult.envelope;
    baseSource.value = assetsResult.source;
    if (!baseInitialized) {
      baseInitialized = true;
      // 首次加载：以接口 default_weight 与数据覆盖区间初始化（缺省 40/40/10/10）。
      if (assets.value.data.assets.length) {
        const next: Record<string, number> = {};
        let first = "";
        let last = "";
        for (const asset of assets.value.data.assets) {
          next[asset.symbol] = asset.default_weight;
          if (asset.data_first_date && (!first || asset.data_first_date > first)) first = asset.data_first_date;
          if (asset.data_last_date && (!last || asset.data_last_date < last)) last = asset.data_last_date;
        }
        weights.value = next;
        if (first) backtestParams.start_date = first;
        if (last) backtestParams.end_date = last;
      }
      void runBacktest();
    }
  } catch (err) {
    assetsError.value = errorMessage(err);
  } finally {
    assetsLoading.value = false;
  }
}

async function runBacktest(): Promise<void> {
  if (!weightsValid.value) {
    backtestError.value = "权重合计必须为 100%，当前配置暂不执行回测。";
    return;
  }
  const seq = ++backtestSeq;
  backtestLoading.value = true;
  backtestError.value = null;
  try {
    const result = await runWealthBacktest({
      weights: { ...weights.value },
      start_date: backtestParams.start_date,
      end_date: backtestParams.end_date,
      rebalance: backtestParams.rebalance,
      cost_bps: backtestParams.cost_bps,
      initial_capital: backtestParams.initial_capital,
      benchmarks: ["EQUAL_WEIGHT", "510300.SH"],
      allow_demo: true,
    });
    if (seq !== backtestSeq) return;
    backtest.value = result.envelope;
    backtestSource.value = result.source;
    risk.value = null;
    try {
      const riskResult = await fetchWealthRisk(result.envelope.data.backtest_id);
      if (seq === backtestSeq) risk.value = riskResult.envelope;
    } catch {
      /* 风险详情加载失败不阻塞回测展示 */
    }
  } catch (err) {
    if (seq === backtestSeq) backtestError.value = errorMessage(err);
  } finally {
    if (seq === backtestSeq) backtestLoading.value = false;
  }
}

/** 权重 / 参数有效变更 → 防抖 800ms 自动重新回测（权重非法时暂停）。 */
watch([weights, () => ({ ...backtestParams })], () => {
  if (!weightsValid.value) return;
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => void runBacktest(), 800);
});

function applyProfile(nextWeights: Record<string, number>): void {
  weights.value = { ...nextWeights };
}

onMounted(loadBase);
onBeforeUnmount(() => {
  if (debounceTimer) clearTimeout(debounceTimer);
  ++backtestSeq;
});
</script>

<template>
  <div class="wealth-lab">
    <div class="page-head">
      <p class="page-sub head-sub">
        四资产月度再平衡组合 · 沪深300 / 国债 / 黄金 / 可转债 · 全部结果仅供研究演示，不构成投资建议
      </p>
      <div class="head-chips">
        <span v-if="offlineMode" class="badge warn">后端未连接 · 契约 Mock 样例</span>
        <span v-else-if="backtest?.data.data_mode === 'DEMO'" class="badge warn">演示数据模式 DEMO</span>
        <span v-else-if="backtest?.data.data_mode === 'REAL'" class="badge ok">真实数据模式 REAL</span>
        <span class="badge action">契约 wealth-lab/v1（冻结）</span>
        <button class="mini" @click="loadBase">{{ assetsLoading ? "刷新中…" : "刷新" }}</button>
      </div>
    </div>

    <div v-if="offlineMode" class="mode-banner" role="status">
      后端 <code>/v1/wealth-lab/*</code> 尚未连接：当前页面使用 ZCode 提供的契约 Mock 样例
      （docs/contracts/mocks）完成布局与交互演示，曲线与指标<strong>不是真实回测</strong>，且不随权重参数变化。
      待 <code>trading/wealth_lab/</code> 路由就绪后将自动切换为真实 API。
    </div>
    <div v-else-if="demoMode" class="mode-banner demo" role="status">
      当前为 <strong>DEMO 合成数据模式</strong>（series.demo=true）：曲线为合成演示序列，禁止用于任何真实结论。
    </div>

    <WealthDashboard :meta="meta" :backtest="backtest" :risk="risk" :source="overallSource" />

    <AllocationCenter
      v-model:weights="weights"
      :assets="assets"
      :assets-loading="assetsLoading"
      :assets-error="assetsError"
      :initial-capital="backtestParams.initial_capital"
    />

    <BacktestCenter
      v-model:params="backtestParams"
      :envelope="backtest"
      :risk="risk"
      :loading="backtestLoading"
      :error="backtestError"
      :assets="assets?.data.assets ?? null"
      @run="runBacktest"
    />

    <StressTestCenter
      :weights="weights"
      :initial-capital="backtestParams.initial_capital"
      :assets="assets?.data.assets ?? null"
      @apply-profile="applyProfile"
    />

    <p class="research-note">research_only · 数据来源 xiaodefa-tushare（fund_daily + fund_adj 双端点核验）· 压力测试阈值均为研究演示规则</p>
  </div>
</template>

<style scoped>
.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.head-sub { margin: 0; }
.head-chips { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mode-banner {
  padding: 11px 14px; margin-bottom: 16px;
  border: 1px solid color-mix(in srgb, var(--warn) 50%, transparent);
  background: color-mix(in srgb, var(--warn) 8%, transparent);
  border-radius: 10px;
  color: var(--banner-text);
  font-size: 12.5px;
  line-height: 1.7;
}
.mode-banner.demo { border-color: color-mix(in srgb, var(--gold) 55%, transparent); }
.mode-banner code { color: var(--accent-2); font-size: 11.5px; }
.mode-banner strong { color: var(--gold); }
.research-note { margin: 4px 0 0; font-size: 11.5px; color: var(--text-dim); text-align: center; }
.wealth-lab :deep(section.panel) { scroll-margin-top: 16px; }
</style>
