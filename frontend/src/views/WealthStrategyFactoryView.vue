<script setup lang="ts">
/**
 * 策略工厂（v2）：S0-S8 策略选择 → 参数覆写（仅提交改动键）→ 统一引擎回测。
 * 契约：POST /v1/wealth-lab/v2/strategies/{strategy_id}/backtest。
 */
import { computed, onMounted, ref } from "vue";
import type { StrategyBacktestData, StrategiesData, UniverseData, WealthEnvelope } from "../types/wealthLabV2";
import { fetchV2Strategies, fetchV2Universe, runV2StrategyBacktest } from "../api/wealthLabV2";
import type { DataSource } from "../api/wealthLab";
import { errorMessage } from "../api/client";
import StrategyBacktestResult from "../components/wealthv2/StrategyBacktestResult.vue";
import { STRATEGY_PARAM_LABELS, strategyFamily } from "../domain/wealthV2Format";
import { fmtPct, REBALANCE_LABELS } from "../domain/wealthFormat";
import { useWealthLabV2Store } from "../stores/wealthLabV2";
import { fetchV3Universes, runV3UniverseBacktest } from "../api/wealthLabV3";
import type { UniversesData, UniverseBacktestData, TimeSemantics, WealthEnvelope as V3Envelope } from "../types/wealthLabV3";
import WlDateInput from "../components/wealth/WlDateInput.vue";
import type { StrategyMeta } from "../types/wealthLabV2";

const strategiesEnvelope = ref<WealthEnvelope<StrategiesData> | null>(null);
const universeEnvelope = ref<WealthEnvelope<UniverseData> | null>(null);
const baseSource = ref<DataSource>("live");
const baseLoading = ref(false);
const baseError = ref<string | null>(null);

const strategies = computed(() => strategiesEnvelope.value?.data.strategies ?? []);
const universe = computed(() => universeEnvelope.value?.data.assets ?? []);

const selectedId = ref<string>("");
const selected = computed(() => strategies.value.find((s) => s.strategy_id === selectedId.value) ?? null);

/** 参数编辑状态：仅 primitive 键可编辑；提交时只带改动键（params_override 部分覆盖语义）。 */
const paramEdits = ref<Record<string, number | string>>({});
/** 固定权重类策略（params.weights 为对象）使用可视化权重控件；原始对象仍保留在参数详情。 */
const weightEdits = ref<Record<string, number>>({});
const editedStrategyId = ref<string>("");
const form = ref({ start_date: "2020-04-08", end_date: "2026-10-23", cost_bps: 5, initial_capital: 1000000 });

const result = ref<WealthEnvelope<StrategyBacktestData> | null>(null);
const resultSource = ref<DataSource>("live");
const loading = ref(false);
const error = ref<string | null>(null);
const errorHint = ref<string | null>(null);
const pitAudit = ref<import("../types/wealthLabV3").PitRebalanceSnapshots | null>(null);
/** v3.2 时间语义（requested/effective/first_investment 三日期；旧后端无此字段时为 null）。 */
const timeSemantics = ref<TimeSemantics | null>(null);
/** PIT 闸门拒绝时的建议起始日期（v3.2 增量；旧后端无此字段时为 null）。 */
const rejected = ref<{ requested: string | null; suggested: string | null } | null>(null);
const wealthLabV2 = useWealthLabV2Store();
let requestSeq = 0;

/* ---------------- v3 动态资产池选择 ---------------- */

const universesEnvelope = ref<V3Envelope<UniversesData> | null>(null);
const universesError = ref<string | null>(null);
const universes = computed(() => universesEnvelope.value?.data.universes ?? []);
/** core4-v1 = v1/v2 基线（走 v2 引擎，结果与既有页面完全一致）；其余池走 v3 引擎。 */
const selectedUniverseId = ref("core4-v1");
const CORE4_BASELINE = "core4-v1";

const selectedUniverse = computed(() =>
  universesEnvelope.value?.data.universes.find((u) => u.universe_id === selectedUniverseId.value) ?? null,
);
const universesSource = ref<DataSource>("live");
const universesSourceMock = computed(() => universesSource.value === "offline-mock");
const usingV3Engine = computed(() => selectedUniverseId.value !== CORE4_BASELINE);

const poolInfo = computed(() => {
  if (!usingV3Engine.value || !selectedUniverse.value) return null;
  return {
    universeId: selectedUniverse.value.universe_id,
    universeVersion: selectedUniverse.value.universe_version,
    engineLabel: "v3 资产池引擎（eligible 过滤后执行）",
    updatedAt: selectedUniverse.value.created_at,
  };
});

const paramEntries = computed(() => {
  if (!selected.value) return [];
  return Object.entries(selected.value.params).filter(([key]) => key !== "weights");
});

const weightParam = computed<Record<string, number> | null>(() => {
  const value = selected.value?.params.weights;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => typeof v === "number");
  return entries.length ? Object.fromEntries(entries) as Record<string, number> : null;
});

const weightSumValue = computed(() =>
  Object.values(weightEdits.value).reduce((acc, v) => acc + (Number.isFinite(v) ? v : 0), 0),
);
const weightSumValid = computed(() =>
  !weightParam.value || Math.abs(weightSumValue.value - 1) <= 1e-6,
);

function weightPct(symbol: string): number {
  return Math.round((weightEdits.value[symbol] ?? 0) * 10000) / 100;
}

function setWeightPct(symbol: string, pct: number): void {
  const clamped = Math.min(100, Math.max(0, Math.round(pct * 100) / 100));
  weightEdits.value = { ...weightEdits.value, [symbol]: clamped / 100 };
}

function resetWeights(): void {
  if (weightParam.value) weightEdits.value = { ...weightParam.value };
}

function assetNameOf(symbol: string): string {
  return universe.value.find((asset) => asset.symbol === symbol)?.name ?? symbol;
}

function isEditable(value: unknown): boolean {
  return typeof value === "number" || typeof value === "string" || typeof value === "boolean";
}

function displayValue(value: unknown): string {
  return JSON.stringify(value);
}

function paramLabel(key: string): string {
  return STRATEGY_PARAM_LABELS[key] ?? key;
}

function selectStrategy(meta: StrategyMeta): void {
  selectedId.value = meta.strategy_id;
  result.value = null;
  error.value = null;
  editedStrategyId.value = meta.strategy_id;
  const edits: Record<string, number | string> = {};
  for (const [key, value] of Object.entries(meta.params)) {
    if (isEditable(value)) edits[key] = value;
  }
  paramEdits.value = edits;
  // weights 对象参数走可视化权重控件
  const weights = meta.params.weights;
  weightEdits.value =
    weights && typeof weights === "object" && !Array.isArray(weights)
      ? Object.fromEntries(
          Object.entries(weights as Record<string, unknown>)
            .filter(([, v]) => typeof v === "number")
            .map(([k, v]) => [k, v as number]),
        )
      : {};
}

function applySuggestedStart(): void {
  if (!rejected.value?.suggested) return;
  form.value = { ...form.value, start_date: rejected.value.suggested };
  void runBacktest();
}

const paramsOverride = computed<Record<string, unknown> | undefined>(() => {
  if (!selected.value) return undefined;
  const override: Record<string, unknown> = {};
  for (const [key, edited] of Object.entries(paramEdits.value)) {
    const original = selected.value.params[key];
    const normalized = typeof original === "number" ? Number(edited) : edited;
    if (normalized !== original) override[key] = normalized;
  }
  // 权重对象：与默认不同才提交
  if (weightParam.value) {
    const changed = Object.keys(weightEdits.value).some(
      (symbol) => Math.abs((weightEdits.value[symbol] ?? 0) - (weightParam.value![symbol] ?? 0)) > 1e-9,
    );
    if (changed && weightSumValid.value) override.weights = { ...weightEdits.value };
  }
  return Object.keys(override).length ? override : undefined;
});

const overrideSummary = computed(() => (paramsOverride.value ? JSON.stringify(paramsOverride.value) : "无（使用默认参数）"));

async function runBacktest(): Promise<void> {
  if (!selected.value) return;
  if (!weightSumValid.value) {
    error.value = "目标权重合计必须为 100%，请调整后重试。";
    return;
  }
  const seq = ++requestSeq;
  loading.value = true;
  error.value = null;
  errorHint.value = null;
  try {
    if (usingV3Engine.value) {
      // v3 动态资产池回测：eligible 过滤后执行，结果含 common_window 与 PIT 快照
      const response = await runV3UniverseBacktest(selected.value.strategy_id, {
        universe_id: selectedUniverseId.value,
        start_date: form.value.start_date,
        end_date: form.value.end_date,
        cost_bps: form.value.cost_bps,
        params_override: paramsOverride.value,
      });
      if (seq !== requestSeq) return;
      resultSource.value = response.source;
      // 200+FAILED（如 PIT 闸门：回测起点无合格资产）按业务失败呈现，不进入结果适配
      if (response.envelope.status === "FAILED" || !("series" in (response.envelope.data as object))) {
        result.value = null;
        pitAudit.value = null;
        timeSemantics.value = null;
        const data = response.envelope.data as { reason?: string; requested_start_date?: string; suggested_start_date?: string | null } | null;
        rejected.value = {
          requested: data?.requested_start_date ?? null,
          suggested: data?.suggested_start_date ?? null,
        };
        error.value = response.envelope.sanitized_error || response.envelope.error_code || "资产池回测不可用";
        errorHint.value = [
          data?.reason ? `${data.reason}。` : "",
          data?.suggested_start_date
            ? `后端建议起始日期：${data.suggested_start_date}（点击可直接采用）。`
            : "后端暂未返回建议起始日期（v3.2 增量字段）。",
          "PIT 闸门要求回测起点时池内存在合格资产：可在「多资产研究池」查看该池历史可投资性。",
        ].join("");
        return;
      }
      const v3 = response.envelope.data as UniverseBacktestData;
      // v3.2 过渡兼容：逐调仓记录可能在顶层或嵌于 pit_rebalance_snapshots
      const records = v3.rebalance_records ?? v3.pit_rebalance_snapshots?.rebalance_records ?? [];
      pitAudit.value = { ...(v3.pit_rebalance_snapshots ?? { note: "", rebalances: 0, eligible_count_min: 0, eligible_count_max: 0 }), rebalance_records: records };
      timeSemantics.value = v3.time_semantics ?? null;
      rejected.value = null;
      // 适配为 v2 结果结构复用同一结果组件（data_quality 由池信息合成，不虚构来源）
      result.value = {
        ...response.envelope,
        data: {
          data_mode: v3.data_mode,
          backtest_id: v3.backtest_id,
          strategy: { ...v3.strategy, rebalance: "monthly" },
          series: v3.series,
          metrics: {
            total_return: v3.metrics.total_return,
            annual_return: v3.metrics.annual_return,
            annual_volatility: v3.metrics.annual_volatility,
            sharpe_ratio: v3.metrics.sharpe_ratio,
            sortino_ratio: v3.metrics.sortino_ratio ?? 0,
            calmar_ratio: v3.metrics.calmar_ratio,
            max_drawdown: v3.metrics.max_drawdown,
            monthly_win_rate: v3.metrics.monthly_win_rate,
            avg_turnover_per_rebalance: v3.metrics.avg_turnover_per_rebalance ?? 0,
            trading_days: v3.metrics.trading_days,
          },
          weight_history: v3.weight_history,
          rebalances: v3.rebalances.map((event) => ({ ...event, trades: [] })),
          data_quality: {
            adjusted: true,
            sources: [`${v3.universe_id} 资产池 · 公共窗口 ${v3.common_window.start_date}~${v3.common_window.end_date}`],
            missing_days: 0,
            caveats: [v3.pit_gate.note],
          },
        },
      } as WealthEnvelope<StrategyBacktestData>;
    } else {
      // v1/v2 基线：行为与既有页面完全一致，不被动态资产池覆盖
      const payload = {
        params_override: paramsOverride.value,
        start_date: form.value.start_date,
        end_date: form.value.end_date,
        cost_bps: form.value.cost_bps,
        initial_capital: form.value.initial_capital,
      };
      const response = await runV2StrategyBacktest(selected.value.strategy_id, payload);
      if (seq !== requestSeq) return;
      result.value = response.envelope;
      resultSource.value = response.source;
      pitAudit.value = null; // 基线回测无 v3 PIT 快照
      timeSemantics.value = null;
      rejected.value = null;
    }
    if (result.value) {
      // 记录最近一次真实回测（含资产池上下文），供 AI 研究与决策包校验后关联
      wealthLabV2.recordStrategyBacktest(
        result.value.data.backtest_id,
        result.value.data.strategy.strategy_id,
        result.value.data.strategy.strategy_version,
        selectedUniverseId.value,
      );
    }
  } catch (err) {
    if (seq === requestSeq) error.value = errorMessage(err);
  } finally {
    if (seq === requestSeq) loading.value = false;
  }
}

onMounted(async () => {
  baseLoading.value = true;
  try {
    const [strategiesResult, universeResult] = await Promise.all([fetchV2Strategies(), fetchV2Universe()]);
    strategiesEnvelope.value = strategiesResult.envelope;
    universeEnvelope.value = universeResult.envelope;
    baseSource.value = strategiesResult.source;
    if (!selectedId.value && strategies.value.length) selectStrategy(strategies.value[0]!);
  } catch (err) {
    baseError.value = errorMessage(err);
  } finally {
    baseLoading.value = false;
  }
  // v3 动态资产池列表独立加载，失败不影响基线回测
  try {
    const v3 = await fetchV3Universes();
    universesEnvelope.value = v3.envelope;
    universesSource.value = v3.source;
  } catch (err) {
    universesError.value = errorMessage(err);
  }
});
</script>

<template>
  <div class="strategy-factory">
    <div class="page-head">
      <p class="page-sub head-sub">统一引擎 · 统一成本与年化口径 · 调仓决策仅使用截至前一交易日的历史窗口（防未来函数）</p>
      <div class="head-chips">
        <span v-if="baseSource === 'offline-mock'" class="badge warn">后端未连接 · 契约 Mock 样例</span>
        <span v-else-if="strategiesEnvelope?.data.data_mode === 'REAL'" class="badge ok">策略库 · 真实数据</span>
        <span class="badge action">契约 wealth-lab/v2</span>
        <span class="badge action">v3 资产池扩展</span>
      </div>
    </div>

    <div v-if="baseError" class="error-box">{{ baseError }}</div>

    <!-- v3 动态资产池选择（默认 v1/v2 基线，基线结果不受影响） -->
    <section class="panel" aria-label="研究资产池">
      <h3>
        研究资产池
        <span class="h3-extra">默认使用原四资产基线；切换动态资产池后走 v3 引擎（eligible 过滤后执行）</span>
        <span v-if="universesError" class="badge warn">资产池列表待接入</span>
        <span v-else-if="universesEnvelope && universesSourceMock" class="badge warn">资产池契约 Mock</span>
      </h3>
      <div class="chips-row">
        <button type="button" class="chip" :class="{ active: selectedUniverseId === CORE4_BASELINE }" @click="selectedUniverseId = CORE4_BASELINE">
          原四资产基准池（v1/v2 基线 · 4 只）
        </button>
        <button
          v-for="u in universes.filter((item) => item.universe_id !== CORE4_BASELINE)"
          :key="u.universe_id"
          type="button"
          class="chip"
          :class="{ active: selectedUniverseId === u.universe_id }"
          @click="selectedUniverseId = u.universe_id"
        >{{ u.name }}（{{ u.members }} 只）</button>
      </div>
      <p v-if="selectedUniverse" class="pool-note">
        <strong>{{ selectedUniverse.name }}</strong> · 版本 v{{ selectedUniverse.universe_version }} · 实际参与回测的资产以 PIT 判定为准
        <span class="dim">（幸存者偏差声明：{{ selectedUniverse.survivorship_bias_note }}）</span>
      </p>
      <p v-else class="pool-note">
        基线池走 v2 统一引擎，历史结果与口径保持不变；动态资产池数据来自 v3 契约接口。
      </p>
    </section>

    <div class="factory-grid">
      <section class="panel list-panel" aria-label="策略列表">
        <h3>策略库 <span class="h3-extra">{{ strategies.length }} 个策略（S0-S8）</span></h3>
        <div class="strategy-list">
          <button
            v-for="meta in strategies"
            :key="meta.strategy_id"
            type="button"
            class="strategy-card"
            :class="{ active: meta.strategy_id === selectedId }"
            @click="selectStrategy(meta)"
          >
            <span class="family">{{ strategyFamily(meta.strategy_id) }}</span>
            <span class="s-head"><strong>{{ meta.name }}</strong><small>{{ meta.strategy_id }}</small></span>
            <span class="s-desc">{{ meta.description }}</span>
            <span class="s-meta">{{ REBALANCE_LABELS[meta.rebalance] }} · 最少历史 {{ meta.min_history_days }} 天</span>
          </button>
        </div>
      </section>

      <section class="panel form-panel" aria-label="回测参数">
        <h3>
          回测参数
          <span v-if="selected" class="h3-extra">{{ selected.asset_selection }}</span>
        </h3>
        <div v-if="selected" class="param-form">
          <div class="form-row">
            <WlDateInput v-model="form.start_date" label="开始日期" />
            <WlDateInput v-model="form.end_date" label="结束日期" />
            <label class="field"><span>单边成本（bps）</span><input v-model.number="form.cost_bps" type="number" min="0" step="1" /></label>
            <label class="field"><span>初始资金（CNY）</span><input v-model.number="form.initial_capital" type="number" min="1000" step="10000" /></label>
          </div>

          <div v-if="weightParam" class="strategy-params weight-editor">
            <h4>目标权重（拖动或输入，合计需为 100%）</h4>
            <div class="weight-rows">
              <div v-for="(value, symbol) in weightParam" :key="symbol" class="weight-row">
                <span class="w-name">{{ assetNameOf(String(symbol)) }}</span>
                <span class="w-symbol">{{ symbol }}</span>
                <input
                  class="w-slider"
                  type="range" min="0" max="100" step="1"
                  :value="weightPct(String(symbol))"
                  :aria-label="`${assetNameOf(String(symbol))} 权重滑杆`"
                  :style="{ accentColor: 'var(--accent)' }"
                  @input="setWeightPct(String(symbol), Number(($event.target as HTMLInputElement).value))"
                />
                <input
                  class="w-input"
                  type="number" min="0" max="100" step="1"
                  :value="weightPct(String(symbol))"
                  :aria-label="`${assetNameOf(String(symbol))} 权重百分比`"
                  @change="setWeightPct(String(symbol), Number(($event.target as HTMLInputElement).value))"
                />
                <span class="w-unit">%</span>
              </div>
            </div>
            <div class="w-sum" :class="weightSumValid ? 'ok' : 'bad'">
              <span>合计：{{ (weightSumValue * 100).toFixed(1) }}%</span>
              <span v-if="!weightSumValid">权重合计必须为 100%，已暂停回测</span>
              <button class="mini" type="button" @click="resetWeights">恢复默认权重</button>
            </div>
          </div>

          <div class="strategy-params">
            <h4>策略参数（params_override，仅提交改动项）</h4>
            <div class="param-grid">
              <label v-for="[key, value] in paramEntries" :key="key" class="field">
                <span>{{ paramLabel(key) }}</span>
                <input
                  v-if="typeof value === 'number'"
                  v-model="paramEdits[key]"
                  type="number"
                  :step="Number.isInteger(value) ? 1 : 0.01"
                />
                <input v-else-if="typeof value === 'string'" v-model="paramEdits[key]" type="text" />
                <span v-else-if="typeof value === 'boolean'" class="static-value">{{ value ? "开" : "关" }}</span>
                <code v-else class="static-value" :title="displayValue(value)">{{ displayValue(value) }}</code>
              </label>
            </div>
            <p class="override-line">params_override：<code>{{ overrideSummary }}</code></p>
            <p v-if="selected.risk_notes" class="risk-note">⚠ 策略风险：{{ selected.risk_notes }}</p>
          </div>

          <div class="actions">
            <button class="mini primary" type="button" :disabled="loading || !weightSumValid" @click="runBacktest">{{ loading ? "回测中…" : "运行策略回测" }}</button>
            <span v-if="selected" class="dim">最少历史 {{ selected.min_history_days }} 个交易日</span>
          </div>
          <p v-if="errorHint" class="error-hint">※ {{ errorHint }}</p>
          <div v-if="rejected?.suggested" class="suggested-row">
            <span>建议起始日期：<b>{{ rejected.suggested }}</b><template v-if="rejected.requested">（原请求 {{ rejected.requested }}）</template></span>
            <button class="mini primary" type="button" :disabled="loading" @click="applySuggestedStart">采用并重跑</button>
          </div>
        </div>
      </section>
    </div>

    <StrategyBacktestResult
      :envelope="result"
      :loading="loading"
      :error="error"
      :universe="universe"
      :pool-info="poolInfo"
      :pit-audit="pitAudit"
      :time-semantics="timeSemantics"
    />
  </div>
</template>

<style scoped>
.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.head-sub { margin: 0; }
.head-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.factory-grid { display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start; margin-bottom: 16px; }
.strategy-list { display: flex; flex-direction: column; gap: 8px; max-height: 560px; overflow-y: auto; padding-right: 2px; }
.strategy-card {
  display: flex; flex-direction: column; gap: 4px; text-align: left;
  border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px;
  background: var(--bg-panel-2); color: var(--text); cursor: pointer;
  transition: border-color 0.15s ease, transform 0.15s ease;
}
.strategy-card:hover { border-color: var(--accent-ring); transform: translateX(2px); }
.strategy-card.active { border-color: var(--accent); background: var(--accent-soft); }
.family { font-size: 10px; color: var(--accent-2); letter-spacing: 1px; }
.s-head { display: flex; align-items: baseline; gap: 8px; }
.s-head strong { font-size: 13.5px; }
.s-head small { color: var(--text-dim); font-size: 10.5px; }
.s-desc { font-size: 11.5px; color: var(--text-dim); }
.s-meta { font-size: 10.5px; color: var(--text-dim); opacity: 0.85; }
.form-panel .param-form { display: flex; flex-direction: column; gap: 14px; }
.form-row { display: flex; gap: 12px; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; color: var(--text-dim); }
.field input { min-width: 130px; }
.strategy-params { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--bg-hover); }
.weight-editor { border-color: var(--accent-ring); }
.weight-rows { display: flex; flex-direction: column; gap: 8px; }
.weight-row { display: grid; grid-template-columns: 110px 92px 1fr 78px 16px; align-items: center; gap: 10px; }
.w-name { font-size: 13px; color: var(--text); }
.w-symbol { font-size: 11px; color: var(--text-dim); font-variant-numeric: tabular-nums; }
.w-slider { width: 100%; height: 4px; cursor: pointer; }
.w-input { width: 72px; text-align: right; font-variant-numeric: tabular-nums; }
.w-unit { color: var(--text-dim); font-size: 12px; }
.w-sum { display: flex; align-items: center; gap: 12px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); font-size: 12.5px; font-variant-numeric: tabular-nums; }
.w-sum.ok { color: var(--down); }
.w-sum.bad { color: var(--warn); }
.w-sum .mini { margin-left: auto; }
.strategy-params h4 { margin: 0 0 10px; font-size: 12.5px; color: var(--text-dim); font-weight: 600; }
.param-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px 14px; }
.static-value { font-size: 12px; color: var(--text); font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px; display: inline-block; }
.override-line { margin: 10px 0 0; font-size: 11.5px; color: var(--text-dim); }
.override-line code { color: var(--accent-2); font-size: 11px; word-break: break-all; }
.risk-note { margin: 8px 0 0; font-size: 11.5px; color: var(--warn); }
.actions { display: flex; align-items: center; gap: 12px; }
button.primary { border-color: var(--accent); color: var(--accent); padding: 8px 18px; }
button.primary:hover:not(:disabled) { background: var(--accent-soft); }
.dim { color: var(--text-dim); font-size: 12px; }
.chips-row { display: flex; flex-wrap: wrap; gap: 8px; }
.pool-note { margin: 10px 0 0; font-size: 12.5px; color: var(--text-dim); line-height: 1.7; }
.pool-note strong { color: var(--text); }
.error-hint { margin: 10px 0 0; font-size: 12.5px; color: var(--warn); line-height: 1.7; }
.suggested-row { display: flex; align-items: center; gap: 12px; margin-top: 8px; font-size: 12.5px; color: var(--text); }
.suggested-row b { color: var(--accent-strong); font-variant-numeric: tabular-nums; }
@media (max-width: 1280px) {
  .factory-grid { grid-template-columns: 1fr; }
}
</style>
