<script setup lang="ts">
/**
 * 策略工厂 · 单策略回测结果（契约 v2 StrategyBacktestResult）。
 * 净值+现金权重双轴图、回撤图、V2Metrics 指标卡、防未来函数权重历史审计表。
 */
import { computed, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type { StrategyBacktestData, WealthEnvelope } from "../../types/wealthLabV2";
import echarts, { type EChartsCoreOption } from "../../charts";
import { useECharts } from "../../composables/useECharts";
import { useChartTheme, withAlpha } from "../../domain/chartTheme";
import { fmtCny, fmtNav, fmtPct, fmtRatio, returnColorClass, assetName as resolveAssetName } from "../../domain/wealthFormat";
import { CASH_WEIGHT_HINT, COST_MODEL_HINT, METRIC_HINTS, METRIC_LABELS, WEIGHT_SNAPSHOT_LABELS } from "../../domain/wealthLabLabels";
import type { UniverseAsset } from "../../types/wealthLabV2";
import type { PitRebalanceSnapshots, TimeSemantics } from "../../types/wealthLabV3";

const props = defineProps<{
  envelope: WealthEnvelope<StrategyBacktestData> | null;
  loading: boolean;
  error: string | null;
  universe: UniverseAsset[];
  /** v3 动态资产池信息（core4 基线回测时为 null）。 */
  poolInfo?: { universeId: string; universeVersion: string; engineLabel: string; updatedAt?: string } | null;
  /** v3.1 逐调仓 PIT 审计快照（仅 v3 引擎提供；core4 基线为 null）。 */
  pitAudit?: PitRebalanceSnapshots | null;
  /** v3.2 时间语义（requested/effective/first_investment 三日期；旧后端可能不返回）。 */
  timeSemantics?: TimeSemantics | null;
}>();

const data = computed(() => props.envelope?.data ?? null);
const series = computed(() => data.value?.series ?? null);
const metrics = computed(() => data.value?.metrics ?? null);

const navEl = ref<HTMLElement | null>(null);
const ddEl = ref<HTMLElement | null>(null);
const navChart = useECharts(navEl as Ref<HTMLElement | null>);
const ddChart = useECharts(ddEl as Ref<HTMLElement | null>);

const ct = useChartTheme();
const tooltipBase = computed(() => ({
  backgroundColor: ct.value.tooltipBg,
  borderColor: ct.value.border,
  textStyle: { color: ct.value.tooltipText, fontSize: 13 } as const,
}));

const navOption = computed<EChartsCoreOption | null>(() => {
  const s = series.value;
  if (!s?.dates.length) return null;
  return {
    animationDuration: 700,
    tooltip: { trigger: "axis", ...tooltipBase.value },
    legend: { top: 0, right: 0, icon: "roundRect", itemWidth: 14, itemHeight: 4, textStyle: { color: ct.value.dim, fontSize: 12.5 } },
    grid: { left: 60, right: 64, top: 36, bottom: 40 },
    xAxis: {
      type: "category",
      data: s.dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: ct.value.axisLine } },
      axisLabel: { color: ct.value.dim, fontSize: 11.5 },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: "净值",
        nameTextStyle: { color: ct.value.dim, fontSize: 11.5 },
        axisLabel: { color: ct.value.dim, fontSize: 12, formatter: (v: number) => v.toFixed(2) },
        splitLine: { lineStyle: { color: ct.value.grid } },
      },
      {
        type: "value",
        min: 0,
        max: 1,
        name: "现金比例",
        nameTextStyle: { color: ct.value.dim, fontSize: 11.5 },
        axisLabel: { color: ct.value.dim, fontSize: 12, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "策略净值",
        type: "line",
        data: s.nav,
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
        endLabel: { show: true, color: ct.value.accent, fontSize: 12, formatter: (p: { value: number }) => fmtNav(p.value) },
      },
      {
        name: "现金权重（未持仓比例）",
        type: "line",
        yAxisIndex: 1,
        data: s.cash_weight,
        showSymbol: false,
        step: "end",
        lineStyle: { width: 1.4, color: ct.value.gold, type: "dashed" },
      },
    ],
  } satisfies EChartsCoreOption;
});

const ddOption = computed<EChartsCoreOption | null>(() => {
  const s = series.value;
  if (!s?.dates.length) return null;
  return {
    animationDuration: 700,
    tooltip: { trigger: "axis", ...tooltipBase.value, valueFormatter: (v: number) => fmtPct(v) },
    grid: { left: 60, right: 20, top: 16, bottom: 26 },
    xAxis: {
      type: "category",
      data: s.dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: ct.value.axisLine } },
      axisLabel: { color: ct.value.dim, fontSize: 11.5 },
    },
    yAxis: {
      type: "value",
      max: 0,
      axisLabel: { color: ct.value.dim, fontSize: 12, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: ct.value.grid } },
    },
    series: [
      {
        name: "策略回撤",
        type: "line",
        data: s.drawdown,
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

watchEffect(() => {
  const opt = navOption.value;
  if (opt) navChart.setOption(opt, true);
});
watchEffect(() => {
  const opt = ddOption.value;
  if (opt) ddChart.setOption(opt, true);
});

const metricItems = computed(() => {
  const m = metrics.value;
  if (!m) return [];
  return [
    { label: METRIC_LABELS.total_return, hint: "", value: fmtPct(m.total_return), cls: returnColorClass(m.total_return) },
    { label: METRIC_LABELS.annual_return, hint: "", value: fmtPct(m.annual_return), cls: returnColorClass(m.annual_return) },
    { label: METRIC_LABELS.annual_volatility, hint: "", value: fmtPct(m.annual_volatility, 2, false), cls: "" },
    { label: METRIC_LABELS.sharpe_ratio, hint: METRIC_HINTS.sharpe_ratio ?? "", value: fmtRatio(m.sharpe_ratio), cls: "" },
    { label: METRIC_LABELS.sortino_ratio, hint: METRIC_HINTS.sortino_ratio ?? "", value: fmtRatio(m.sortino_ratio), cls: "" },
    { label: METRIC_LABELS.calmar_ratio, hint: METRIC_HINTS.calmar_ratio ?? "", value: fmtRatio(m.calmar_ratio), cls: "" },
    { label: METRIC_LABELS.max_drawdown, hint: METRIC_HINTS.max_drawdown ?? "", value: fmtPct(m.max_drawdown), cls: "down" },
    { label: METRIC_LABELS.monthly_win_rate, hint: "", value: fmtPct(m.monthly_win_rate, 1, false), cls: "" },
    { label: METRIC_LABELS.avg_turnover_per_rebalance, hint: METRIC_HINTS.avg_turnover_per_rebalance ?? "", value: fmtPct(m.avg_turnover_per_rebalance, 2, false), cls: "" },
    { label: METRIC_LABELS.trading_days, hint: "", value: String(m.trading_days), cls: "" },
  ];
});

function assetName(symbol: string): string {
  return resolveAssetName(props.universe, symbol);
}

const recentWeightHistory = computed(() => {
  const history = data.value?.weight_history ?? [];
  return history.slice(-8).reverse();
});
const totalCost = computed(() => (data.value?.rebalances ?? []).reduce((acc, event) => acc + event.cost, 0));

/** 剩余现金权重（序列最后一个非空值）。 */
const latestCashWeight = computed(() => {
  const cash = data.value?.series.cash_weight ?? [];
  return cash.length ? cash[cash.length - 1]! : null;
});

/* ---------------- v3.2 逐调仓完整审计（后端可选返回） ---------------- */

const rebalanceRecords = computed(() => props.pitAudit?.rebalance_records ?? []);

/** v3.2 过渡兼容：首末快照的排除映射可能是旧 excluded 或新 excluded_reasons，且可能缺省。 */
const firstExcluded = computed<Record<string, string[]> | null>(() => {
  const first = props.pitAudit?.first;
  if (!first) return null;
  return (first.excluded ?? first.excluded_reasons ?? null) as Record<string, string[]> | null;
});
const lastExcluded = computed<Record<string, string[]> | null>(() => {
  const last = props.pitAudit?.last;
  if (!last) return null;
  return (last.excluded ?? last.excluded_reasons ?? null) as Record<string, string[]> | null;
});

const auditChartEl = ref<HTMLElement | null>(null);
const auditChart = useECharts(auditChartEl);

/** 逐调仓合格资产数折线（records 存在时渲染；与净值守格对齐展示）。 */
const auditOption = computed<EChartsCoreOption | null>(() => {
  const records = rebalanceRecords.value;
  if (!records.length) return null;
  return {
    animationDuration: 400,
    tooltip: { trigger: "axis" },
    grid: { left: 44, right: 16, top: 18, bottom: 26 },
    xAxis: { type: "category", data: records.map((r) => r.decision_date), axisLabel: { color: ct.value.dim, fontSize: 10 } },
    yAxis: { type: "value", minInterval: 1, axisLabel: { color: ct.value.dim, fontSize: 10 } },
    series: [
      {
        name: "PIT 合格资产数",
        type: "line",
        data: records.map((r) => r.eligible_assets.length),
        step: "end",
        showSymbol: false,
        lineStyle: { width: 1.8, color: ct.value.accent },
        areaStyle: { color: withAlpha(ct.value.accent, 0.1) },
      },
    ],
  } satisfies EChartsCoreOption;
});

watchEffect(() => {
  const opt = auditOption.value;
  if (opt) auditChart.setOption(opt, true);
});
</script>

<template>
  <section class="panel" aria-labelledby="wl2-sb-title">
    <h3 id="wl2-sb-title">
      策略回测结果
      <span v-if="data" class="h3-extra">{{ data.strategy.name }} · {{ data.strategy.strategy_id }} v{{ data.strategy.strategy_version }}</span>
      <span v-if="data?.data_mode === 'REAL'" class="badge ok">真实数据</span>
      <span v-else-if="data?.data_mode === 'DEMO'" class="badge warn">演示数据</span>
    </h3>

    <div v-if="error" class="error-box">{{ error }}</div>

    <div v-if="props.poolInfo" class="pool-strip">
      <span><b>资产池</b>{{ props.poolInfo.universeId }}（v{{ props.poolInfo.universeVersion }}）</span>
      <span><b>池版本更新时间</b>{{ props.poolInfo.updatedAt ?? "—" }}</span>
      <span><b>引擎</b>{{ props.poolInfo.engineLabel }}</span>
      <span class="dim">实际参与回测的资产以 PIT 判定为准，详见「多资产研究池」；v1/v2 基线结果不受动态资产池影响</span>
    </div>

    <template v-if="data && series?.dates.length">
      <div class="chart-caption">
        <strong>策略净值与现金权重</strong>
        <span>{{ CASH_WEIGHT_HINT }}</span>
      </div>
      <div ref="navEl" class="chart nav-chart"></div>

      <div class="lower-grid">
        <div>
          <div class="chart-caption"><strong>历史回撤</strong><span>净值相对历史高点的最大回落幅度</span></div>
          <div ref="ddEl" class="chart dd-chart"></div>
        </div>
        <div class="metric-grid">
          <div v-for="item in metricItems" :key="item.label" class="m-item" :title="item.hint">
            <span>{{ item.label }}</span>
            <b :class="item.cls">{{ item.value }}</b>
          </div>
        </div>
      </div>

      <div class="audit-row">
        <div class="audit-info">
          <strong>防未来函数审计</strong>
          <p>每次调仓均记录「决策数据截止日」：调仓决策只使用该日期（含）之前的历史行情，杜绝用未来数据生成信号。</p>
          <span class="dim">{{ COST_MODEL_HINT }}</span>
          <span class="dim">调仓 {{ data.rebalances.length }} 次 · 累计交易成本 {{ fmtCny(totalCost, 2) }}</span>
          <span class="dim">{{ data.data_quality.adjusted ? "净值口径：后复权（分红再投资）" : "净值口径：未复权" }} · 交易日缺口 {{ data.data_quality.missing_days }} 天</span>
        </div>
        <div class="table-scroll weight-table">
          <table class="data">
            <thead>
              <tr><th>{{ WEIGHT_SNAPSHOT_LABELS.date }}</th><th>{{ WEIGHT_SNAPSHOT_LABELS.decision_data_through }}</th><th>{{ WEIGHT_SNAPSHOT_LABELS.weights }}</th></tr>
            </thead>
            <tbody>
              <tr v-for="snapshot in recentWeightHistory" :key="snapshot.date">
                <td>{{ snapshot.date }}</td>
                <td>{{ snapshot.decision_data_through }}</td>
                <td>
                  <span v-for="(weight, symbol) in snapshot.weights" :key="symbol" class="w-chip">
                    {{ assetName(String(symbol)) }} {{ fmtPct(weight, 1, false) }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <p class="quality-line">
        <template v-for="caveat in data.data_quality.caveats" :key="caveat">※ {{ caveat }} </template>
      </p>

      <!-- v3.1/v3.2 历史调仓与可投资性审计（PIT） -->
      <details v-if="pitAudit" class="pit-audit">
        <summary>历史调仓与可投资性审计（PIT · 每个调仓决策时点由后端重新判定资格）</summary>
        <div class="pit-body">
          <!-- v3.2 时间语义：消除回测日期歧义 -->
          <div class="time-strip">
            <span><b>请求起点</b>{{ timeSemantics?.requested_start_date ?? "—" }}<em v-if="!timeSemantics" class="gold-text">（后端未提供）</em></span>
            <span><b>实际首日</b>{{ timeSemantics?.effective_start_date ?? "—" }}<em v-if="!timeSemantics" class="gold-text">（后端未提供）</em></span>
            <span><b>首次建仓</b>{{ timeSemantics ? (timeSemantics.first_investment_date ?? "首期持币") : "—" }}<em v-if="!timeSemantics" class="gold-text">（后端未提供）</em></span>
            <span><b>实际交易日</b>{{ timeSemantics?.actual_trading_days ?? "—" }}</span>
            <span v-if="timeSemantics?.start_shift_reason" class="dim">※ {{ timeSemantics.start_shift_reason }}</span>
            <span v-else-if="timeSemantics" class="dim">※ {{ timeSemantics.note }}</span>
          </div>
          <div class="pit-summary">
            <div class="pit-stat"><span>调仓总次数</span><b>{{ pitAudit.rebalances }}</b></div>
            <div class="pit-stat"><span>合格资产数量区间</span><b>{{ pitAudit.eligible_count_min }} ~ {{ pitAudit.eligible_count_max }}</b></div>
            <div class="pit-stat"><span>剩余现金权重（最新）</span><b>{{ fmtPct(latestCashWeight, 1, false) }}</b></div>
            <div class="pit-stat">
              <span>逐调仓合格数量序列</span>
              <b v-if="rebalanceRecords.length" class="up">已提供（{{ rebalanceRecords.length }} 条）</b>
              <b v-else class="gold-text">未提供</b>
            </div>
          </div>
          <p v-if="!rebalanceRecords.length" class="pit-note">处理方式：{{ pitAudit.note }}。逐调仓合格数量序列后端仅提供首末快照与区间，未提供完整序列，此处不作推算（v3.2 契约交付后自动升级为完整审计）。</p>
          <template v-else>
            <div class="chart-caption"><strong>逐调仓 PIT 合格资产数</strong><span>每个调仓决策时点后端实时判定（step 线，非插值）</span></div>
            <div ref="auditChartEl" class="audit-chart"></div>
            <div class="table-scroll records-table">
              <table class="data">
                <thead>
                  <tr>
                    <th>决策日期</th><th>决策数据截止</th><th class="num">PIT 合格</th><th class="num">实际入选</th><th class="num">现金仓位</th><th class="num">成本(CNY)</th><th>目标权重</th><th>被排除（原因）</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="record in rebalanceRecords" :key="record.decision_date">
                    <td>{{ record.decision_date }}</td>
                    <td>{{ record.decision_data_through }}</td>
                    <td class="num">{{ record.eligible_assets.length }}</td>
                    <td class="num">{{ record.selected_assets.length }}</td>
                    <td class="num">{{ fmtPct(record.cash_weight, 1, false) }}</td>
                    <td class="num">{{ record.cost ? record.cost.toFixed(2) : "—" }}</td>
                    <td>
                      <span v-for="(weight, symbol) in record.target_weights" :key="symbol" class="w-chip">{{ assetName(String(symbol)) }} {{ fmtPct(weight, 1, false) }}</span>
                      <span v-if="!Object.keys(record.target_weights).length" class="dim-line">持币</span>
                    </td>
                    <td class="reason-cell">
                      <template v-if="record.excluded_reasons && Object.keys(record.excluded_reasons).length">
                        <div v-for="(reasons, symbol) in record.excluded_reasons" :key="symbol" class="pit-exclude">
                          <span class="w-symbol">{{ symbol }}</span>
                          <span v-for="reason in reasons" :key="reason" class="reason-chip">{{ reason }}</span>
                        </div>
                      </template>
                      <template v-else>—</template>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
          <div class="pit-points">
            <div class="r-block">
              <h4>首次调仓快照 · {{ pitAudit.first?.decision_date ?? "未提供" }}（合格 {{ pitAudit.first?.eligible_count ?? pitAudit.first?.eligible_assets?.length ?? "—" }} 只）</h4>
              <template v-if="firstExcluded && Object.keys(firstExcluded).length">
                <div v-for="(reasons, symbol) in firstExcluded" :key="symbol" class="pit-exclude">
                  <span class="w-symbol">{{ symbol }}</span>
                  <span v-for="reason in reasons" :key="reason" class="reason-chip">{{ reason }}</span>
                </div>
              </template>
              <p v-else class="dim-line">无被排除资产。</p>
            </div>
            <div class="r-block">
              <h4>最近调仓快照 · {{ pitAudit.last?.decision_date ?? "未提供" }}（合格 {{ pitAudit.last?.eligible_count ?? pitAudit.last?.eligible_assets?.length ?? "—" }} 只）</h4>
              <template v-if="lastExcluded && Object.keys(lastExcluded).length">
                <div v-for="(reasons, symbol) in lastExcluded" :key="symbol" class="pit-exclude">
                  <span class="w-symbol">{{ symbol }}</span>
                  <span v-for="reason in reasons" :key="reason" class="reason-chip">{{ reason }}</span>
                </div>
              </template>
              <p v-else class="dim-line">无被排除资产。</p>
            </div>
          </div>
          <p class="dim-line">⚠ 每次调仓的完整资格明细以后端响应为准；幸存者偏差未被消除、仅被如实记录（见上方数据口径说明）。</p>
        </div>
      </details>
    </template>
    <div v-else-if="!loading && !error" class="empty">选择策略并设置参数后运行回测。</div>
  </section>
</template>

<style scoped>
.chart-caption { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 6px 0 2px; }
.pool-strip {
  display: flex; flex-wrap: wrap; align-items: center; gap: 16px;
  margin-bottom: 12px; padding: 9px 14px;
  border: 1px solid var(--accent-ring); border-radius: 10px;
  background: var(--accent-softer); font-size: 13px; font-variant-numeric: tabular-nums;
}
.pool-strip b { color: var(--text-dim); font-weight: 600; margin-right: 6px; }
.pool-strip .dim { color: var(--text-dim); font-size: 12px; }
.chart-caption strong { font-size: 14px; }
.chart-caption span { font-size: 12px; color: var(--text-dim); }
.chart { width: 100%; }
.nav-chart { height: 330px; }
.dd-chart { height: 225px; }
.lower-grid { display: grid; grid-template-columns: 1.15fr 1fr; gap: 16px; margin-top: 8px; }
.metric-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px 14px; align-content: start; border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; background: var(--bg-hover); }
.m-item { display: flex; flex-direction: column; gap: 3px; }
.m-item span { font-size: 12px; color: var(--text-dim); }
.m-item b { font-size: 17px; font-variant-numeric: tabular-nums; }
.audit-row { display: grid; grid-template-columns: 320px 1fr; gap: 14px; margin-top: 14px; align-items: start; }
.audit-info { display: flex; flex-direction: column; gap: 7px; border: 1px dashed var(--accent-ring); border-radius: 10px; padding: 12px 14px; }
.audit-info strong { font-size: 13.5px; color: var(--accent-2); }
.audit-info p { margin: 0; font-size: 12.5px; color: var(--text-dim); line-height: 1.7; }
.audit-info .dim { font-size: 12px; color: var(--text-dim); }
.weight-table { max-height: 260px; }
.weight-table th, .weight-table td { font-size: 13.5px; padding: 10px 12px; }
.w-chip { display: inline-block; margin: 2px 6px 2px 0; padding: 2px 9px; border: 1px solid var(--border); border-radius: 8px; font-size: 12px; color: var(--text); background: var(--bg-hover); font-variant-numeric: tabular-nums; }
.quality-line { margin: 10px 0 0; font-size: 12px; color: var(--text-dim); }
.pit-audit { margin-top: 14px; border: 1px dashed var(--accent-ring); border-radius: 10px; padding: 10px 14px; }
.time-strip {
  display: flex; flex-wrap: wrap; align-items: center; gap: 16px;
  padding: 9px 14px; margin-bottom: 2px;
  border: 1px solid var(--accent-ring); border-radius: 10px;
  background: var(--accent-softer); font-size: 13px; font-variant-numeric: tabular-nums;
}
.time-strip b { color: var(--text-dim); font-weight: 600; margin-right: 6px; }
.time-strip em { font-style: normal; font-size: 11px; margin-left: 4px; }
.time-strip .dim { font-size: 12px; color: var(--text-dim); }
.audit-chart { height: 180px; margin: 4px 0 10px; }
.records-table { max-height: 360px; }
.records-table th, .records-table td { font-size: 12.5px; padding: 8px 10px; vertical-align: top; }
.reason-cell { max-width: 300px; white-space: normal; }
.pit-audit summary { cursor: pointer; color: var(--accent-2); font-size: 13px; user-select: none; }
.pit-body { margin-top: 12px; display: flex; flex-direction: column; gap: 12px; }
.pit-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.pit-stat { display: flex; flex-direction: column; gap: 2px; border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; background: var(--bg-hover); }
.pit-stat span { font-size: 11.5px; color: var(--text-dim); }
.pit-stat b { font-size: 16px; font-variant-numeric: tabular-nums; }
.pit-note { margin: 0; font-size: 12.5px; color: var(--text); line-height: 1.7; }
.pit-points { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.pit-exclude { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
@media (max-width: 1280px) {
  .pit-points, .pit-summary { grid-template-columns: 1fr; }
}
@media (max-width: 1280px) {
  .lower-grid, .audit-row { grid-template-columns: 1fr; }
}
</style>
