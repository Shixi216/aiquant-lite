<script setup lang="ts">
/**
 * 区域 A · 财富管理驾驶舱
 * 组合核心指标（总资产/累计收益/年化波动/最大回撤/夏普）+ 风险概览 + 数据来源与更新状态。
 * 所有数字来源于契约响应（BacktestResponse / RiskResponse / MetaResponse），禁止硬编码。
 */
import { computed } from "vue";
import type { Ref } from "vue";
import type { BacktestData, MetaData, RiskData, WealthEnvelope } from "../../types/wealthLab";
import type { DataSource } from "../../api/wealthLab";
import {
  fmtCny,
  fmtNav,
  fmtPct,
  fmtRatio,
  returnColorClass,
} from "../../domain/wealthFormat";
import { formatDateTime } from "../../domain/labels";
import { dataModeBadge } from "../../domain/wealthLabLabels";

const props = defineProps<{
  meta: WealthEnvelope<MetaData> | null;
  backtest: WealthEnvelope<BacktestData> | null;
  risk: WealthEnvelope<RiskData> | null;
  source: DataSource;
}>();

const data = computed(() => props.backtest?.data ?? null);
const metrics = computed(() => data.value?.metrics ?? null);
const quality = computed(() => data.value?.data_quality ?? null);
const pool = computed(() => props.meta?.data.pool ?? null);

const initialCapital = computed(() => data.value?.params.initial_capital ?? null);
const finalNav = computed(() => {
  const nav = data.value?.series.portfolio_nav;
  return nav?.length ? nav[nav.length - 1]! : null;
});
const totalAssets = computed(() =>
  initialCapital.value !== null && finalNav.value !== null ? initialCapital.value * finalNav.value : null,
);

const totalCost = computed(() =>
  data.value ? data.value.rebalances.reduce((acc, event) => acc + event.cost, 0) : null,
);

const worstDay = computed(() => props.risk?.data.worst_days[0] ?? null);
const var95 = computed(() => props.risk?.data.value_at_risk_95_1d ?? null);

const modeBadge = computed(() => {
  if (props.source === "offline-mock") return { text: "契约 Mock 样例", cls: "badge warn" };
  return dataModeBadge(data.value?.data_mode);
});

// 契约 source 字符串本身面向展示（如 xiaodefa-tushare/fund_daily），直接呈现，不做二次映射。
const sourceLine = computed(() => quality.value?.sources.join(" + ") ?? "—");

const caveats = computed(() => quality.value?.caveats ?? []);
const warnings = computed(() => props.backtest?.warnings ?? []);
</script>

<template>
  <section class="panel dash-panel" aria-labelledby="wl-dash-title">
    <h3 id="wl-dash-title">
      财富管理驾驶舱
      <span class="h3-extra">月度再平衡 · 多资产组合（研究演示口径）</span>
      <span :class="modeBadge.cls">{{ modeBadge.text }}</span>
    </h3>

    <div class="stat-grid hero-grid">
      <div class="stat-card">
        <span class="stat-label">组合总资产</span>
        <span class="stat-value hero">{{ fmtCny(totalAssets) }}</span>
        <span class="stat-foot">初始资金 {{ fmtCny(initialCapital) }} · 期末净值 {{ fmtNav(finalNav) }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">累计收益率</span>
        <span class="stat-value" :class="returnColorClass(metrics?.total_return)">{{ fmtPct(metrics?.total_return) }}</span>
        <span class="stat-foot">年化 {{ fmtPct(metrics?.annual_return) }} · {{ metrics?.trading_days ?? "—" }} 个交易日</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">年化波动率</span>
        <span class="stat-value">{{ fmtPct(metrics?.annual_volatility, 2, false) }}</span>
        <span class="stat-foot">月度胜率 {{ fmtPct(metrics?.monthly_win_rate, 1, false) }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">最大回撤</span>
        <span class="stat-value down">{{ fmtPct(metrics?.max_drawdown) }}</span>
        <span class="stat-foot">卡玛比率 {{ fmtRatio(metrics?.calmar_ratio) }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">夏普比率</span>
        <span class="stat-value">{{ fmtRatio(metrics?.sharpe_ratio) }}</span>
        <span class="stat-foot">无风险利率 rf = 2%/年</span>
      </div>
    </div>

    <div class="dash-lower">
      <div class="sub-panel">
        <h4>当前组合风险概览</h4>
        <template v-if="risk?.data">
          <div class="mini-grid">
            <div class="mini-stat">
              <span class="mini-label">1日 VaR95（历史法）</span>
              <span class="mini-value down">{{ fmtPct(var95) }}</span>
            </div>
            <div class="mini-stat">
              <span class="mini-label">最差单日</span>
              <span class="mini-value down">{{ fmtPct(worstDay?.daily_return) }}</span>
              <span class="mini-foot">{{ worstDay?.date ?? "—" }}</span>
            </div>
            <div class="mini-stat">
              <span class="mini-label">累计调仓成本</span>
              <span class="mini-value">{{ fmtCny(totalCost, 2) }}</span>
              <span class="mini-foot">调仓 {{ data?.rebalances.length ?? 0 }} 次 · 单边 {{ data?.params.cost_bps ?? "—" }} bps</span>
            </div>
            <div class="mini-stat">
              <span class="mini-label">数据复权口径</span>
              <span class="mini-value">{{ quality?.adjusted ? "后复权" : "未复权" }}</span>
              <span class="mini-foot">共同交易日缺口 {{ quality?.missing_days ?? "—" }} 天</span>
            </div>
          </div>
        </template>
        <p v-else class="sub-empty">风险详情待回测完成后加载（VaR95 / 最差单日 / 滚动指标）。</p>
        <p v-if="risk?.warnings.length" class="warn-line">{{ risk.warnings.join("；") }}</p>
      </div>

      <div class="sub-panel">
        <h4>数据来源与更新状态</h4>
        <dl class="meta-list">
          <div><dt>数据来源</dt><dd>{{ sourceLine }}</dd></div>
          <div><dt>数据池</dt><dd>{{ pool?.database ?? "—" }}（{{ pool?.row_count?.toLocaleString("zh-CN") ?? "—" }} 行）</dd></div>
          <div><dt>覆盖区间</dt><dd>{{ pool?.first_date ?? "—" }} ~ {{ pool?.last_date ?? "—" }}</dd></div>
          <div><dt>数据截止</dt><dd>{{ formatDateTime(props.backtest?.data_cutoff ?? null) }}</dd></div>
          <div><dt>生成时间</dt><dd>{{ formatDateTime(props.backtest?.generated_at ?? null) }}</dd></div>
          <div><dt>契约 / 引擎</dt><dd>{{ props.meta?.data.contract_version ?? "wealth-lab/v1" }} · {{ props.meta?.data.engine_version ?? "—" }}</dd></div>
        </dl>
        <p v-for="caveat in caveats" :key="caveat" class="caveat">※ {{ caveat }}</p>
        <p v-for="warning in warnings" :key="warning" class="warn-line">⚠ {{ warning }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.dash-panel { margin-bottom: 16px; }
.hero-grid { grid-template-columns: repeat(5, 1fr); margin-bottom: 14px; }
.dash-lower { display: grid; grid-template-columns: 1.05fr 1fr; gap: 12px; }
.sub-panel {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(10, 14, 20, 0.5);
  padding: 12px 14px;
}
.sub-panel h4 { margin: 0 0 10px; font-size: 12.5px; color: var(--text-dim); font-weight: 600; letter-spacing: 0.5px; }
.sub-empty { margin: 6px 0; color: var(--text-dim); font-size: 12.5px; }
.mini-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.mini-stat { display: flex; flex-direction: column; gap: 3px; }
.mini-label { font-size: 11.5px; color: var(--text-dim); }
.mini-value { font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }
.mini-foot { font-size: 11px; color: var(--text-dim); }
.meta-list { margin: 0; display: flex; flex-direction: column; gap: 7px; }
.meta-list div { display: flex; gap: 10px; font-size: 12.5px; }
.meta-list dt { flex-shrink: 0; width: 76px; color: var(--text-dim); }
.meta-list dd { margin: 0; color: var(--text); word-break: break-all; }
.caveat { margin: 8px 0 0; font-size: 11.5px; color: var(--text-dim); }
.warn-line { margin: 8px 0 0; font-size: 11.5px; color: var(--warn); }
@media (max-width: 1280px) {
  .hero-grid { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
  .dash-lower { grid-template-columns: 1fr; }
}
</style>
