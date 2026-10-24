<script setup lang="ts">
/**
 * AI 多资产研究与独立风控（v2）。
 * 链路：AI 研究 → AI 风险复核（证据核查/幻觉识别）→ 独立风险核查（硬规则 VETO，AI 仅解释）→ 决策包（不可变版本+内容哈希）。
 * 硬风险规则由确定性程序执行；AI 输出恒为 research_only，不改变 VETO 结果。
 * 展示约定：中文主界面；技术 ID/哈希/模型调用号收进「技术审计」折叠区，不喧宾夺主。
 */
import { computed, onMounted, reactive, ref } from "vue";
import type {
  AiResearchData,
  AiRiskReviewData,
  DecisionPacketData,
  RiskCheckData,
  StrategiesData,
  WealthEnvelope,
} from "../types/wealthLabV2";
import {
  createV2DecisionPacket,
  fetchV2Strategies,
  runV2AiResearch,
  runV2AiRiskReview,
  runV2RiskCheck,
} from "../api/wealthLabV2";
import type { DataSource } from "../api/wealthLab";
import { errorMessage } from "../api/client";
import { fmtPct, fmtRatio } from "../domain/wealthFormat";
import { DEFAULT_ASSET_ORDER, DEFAULT_WEIGHTS } from "../domain/wealthFormat";
import {
  AI_REVIEW_VERDICT_LABELS,
  AI_REVIEW_VERDICT_NOTES,
  RISK_CATEGORY_LABELS,
  RISK_SEVERITY_LABELS,
  RISK_VERDICT_CLS,
  RISK_VERDICT_LABELS,
  riskRuleLabel,
} from "../domain/wealthLabLabels";
import { useWealthLabV2Store } from "../stores/wealthLabV2";

const strategiesEnvelope = ref<WealthEnvelope<StrategiesData> | null>(null);
const strategies = computed(() => strategiesEnvelope.value?.data.strategies ?? []);
const baseError = ref<string | null>(null);
const wealthLabV2 = useWealthLabV2Store();

/* ---------------- AI 研究 ---------------- */

const researchForm = reactive({ strategy_id: "", backtest_id: "", context: "" });
const research = ref<WealthEnvelope<AiResearchData> | null>(null);
const researchLoading = ref(false);
const researchError = ref<string | null>(null);
const researchSource = ref<DataSource>("live");

async function runResearch(): Promise<void> {
  researchLoading.value = true;
  researchError.value = null;
  try {
    const payload: Record<string, unknown> = {};
    if (researchForm.strategy_id) payload.strategy_id = researchForm.strategy_id;
    if (researchForm.backtest_id.trim()) payload.backtest_id = researchForm.backtest_id.trim();
    if (researchForm.context.trim()) payload.context = researchForm.context.trim();
    const response = await runV2AiResearch(payload as Parameters<typeof runV2AiResearch>[0]);
    research.value = response.envelope;
    researchSource.value = response.source;
    // 研究完成 → 自动带入复核与决策包引用
    reviewForm.research_id = response.envelope.data.research_id;
    packetForm.research_id = response.envelope.data.research_id;
  } catch (err) {
    researchError.value = errorMessage(err);
  } finally {
    researchLoading.value = false;
  }
}

const reviewClaims = computed<string[]>(() => {
  const data = research.value?.data;
  if (!data) return [];
  return [data.summary, ...data.key_risks];
});

/* ---------------- AI 风险复核 ---------------- */

const reviewForm = reactive({ research_id: "" });
const review = ref<WealthEnvelope<AiRiskReviewData> | null>(null);
const reviewLoading = ref(false);
const reviewError = ref<string | null>(null);

async function runReview(): Promise<void> {
  reviewLoading.value = true;
  reviewError.value = null;
  try {
    const response = await runV2AiRiskReview({
      research_id: reviewForm.research_id || undefined,
      claims: reviewClaims.value.length ? reviewClaims.value : undefined,
    });
    review.value = response.envelope;
    packetForm.risk_review_id = response.envelope.data.review_id;
  } catch (err) {
    reviewError.value = errorMessage(err);
  } finally {
    reviewLoading.value = false;
  }
}

const reviewVerdictCls = computed(() => {
  const verdict = review.value?.data.verdict;
  if (verdict === "SUPPORTED") return "badge ok";
  if (verdict === "CONTRADICTED") return "badge veto";
  return "badge warn";
});

const reviewNote = computed(() => {
  const verdict = review.value?.data.verdict;
  return verdict ? AI_REVIEW_VERDICT_NOTES[verdict] : "";
});

/** 操作流程指引：五步，随完成状态点亮。 */
const flowSteps = computed(() => [
  { label: "选择策略", done: !!researchForm.strategy_id },
  { label: "运行 AI 研究", done: !!research.value },
  { label: "风险复核", done: !!review.value },
  { label: "风险检查", done: !!riskCheck.value },
  { label: "生成决策包", done: !!packet.value },
]);

/* ---------------- 独立风险核查（硬规则 VETO） ---------------- */

const checkWeights = reactive<Record<string, number>>({ ...DEFAULT_WEIGHTS });
const checkForm = reactive({ backtest_id: "" });
const riskCheck = ref<WealthEnvelope<RiskCheckData> | null>(null);
const checkLoading = ref(false);
const checkError = ref<string | null>(null);

const weightEntries = computed(() => DEFAULT_ASSET_ORDER.map((symbol) => [symbol, checkWeights[symbol] ?? 0] as const));
const weightSum = computed(() => Object.values(checkWeights).reduce((acc, v) => acc + (Number.isFinite(v) ? v : 0), 0));

async function runCheck(): Promise<void> {
  checkLoading.value = true;
  checkError.value = null;
  try {
    const payload: Record<string, unknown> = {
      weights: { ...checkWeights },
      portfolio_context: {
        ai_claims: reviewClaims.value.length ? reviewClaims.value : undefined,
      },
    };
    if (checkForm.backtest_id.trim()) payload.backtest_id = checkForm.backtest_id.trim();
    const response = await runV2RiskCheck(payload as Parameters<typeof runV2RiskCheck>[0]);
    riskCheck.value = response.envelope;
    packetForm.risk_check = { verdict: response.envelope.data.verdict, policy_version: response.envelope.data.policy_version };
  } catch (err) {
    checkError.value = errorMessage(err);
  } finally {
    checkLoading.value = false;
  }
}

/* ---------------- 决策包 ---------------- */

const packetForm = reactive({
  strategy_id: "",
  strategy_version: "",
  backtest_id: "",
  research_id: "",
  risk_review_id: "",
  risk_check: null as Record<string, unknown> | null,
});
const packet = ref<WealthEnvelope<DecisionPacketData> | null>(null);
const packetLoading = ref(false);
const packetError = ref<string | null>(null);

const packetWeights = computed<Record<string, number>>(() => {
  const adjusted = riskCheck.value?.data.adjusted_weights;
  return adjusted && Object.keys(adjusted).length ? adjusted : { ...checkWeights };
});

async function createPacket(): Promise<void> {
  if (!packetForm.strategy_id) {
    packetError.value = "请先选择策略（决策包必须绑定策略）。";
    return;
  }
  // 关联校验：回测上下文（资产池 + 策略）必须与所选策略一致，不能只因"最近运行"就默认匹配
  const last = wealthLabV2.lastStrategyBacktest;
  if (last && packetForm.backtest_id.trim() === last.backtestId && packetForm.strategy_id !== last.strategyId) {
    packetError.value = `关联回测 ${last.backtestId} 的策略上下文为 ${last.strategyId}，与当前所选策略 ${packetForm.strategy_id} 不一致。请改选策略，或更换/清空回测编号后重新生成。`;
    return;
  }
  packetLoading.value = true;
  packetError.value = null;
  try {
    const strategy = strategies.value.find((s) => s.strategy_id === packetForm.strategy_id);
    const payload: Record<string, unknown> = {
      strategy_id: packetForm.strategy_id,
      weights: packetWeights.value,
    };
    if (strategy) payload.strategy_version = strategy.strategy_version;
    if (packetForm.backtest_id.trim()) payload.backtest_id = packetForm.backtest_id.trim();
    if (packetForm.research_id.trim()) payload.research_id = packetForm.research_id.trim();
    if (packetForm.risk_check) payload.risk_check = packetForm.risk_check;
    const response = await createV2DecisionPacket(payload as Parameters<typeof createV2DecisionPacket>[0]);
    packet.value = response.envelope;
  } catch (err) {
    packetError.value = errorMessage(err);
  } finally {
    packetLoading.value = false;
  }
}

const hasPacketMetrics = computed(() => Object.keys(packet.value?.data.metrics ?? {}).length > 0);

const packetMetricItems = computed(() => {
  const metrics = packet.value?.data.metrics ?? {};
  return [
    { label: "累计收益率", value: fmtPct(metrics.total_return ?? null) },
    { label: "年化收益率", value: fmtPct(metrics.annual_return ?? null) },
    { label: "年化波动率", value: fmtPct(metrics.annual_volatility ?? null, 2, false) },
    { label: "夏普比率 Sharpe", value: fmtRatio(metrics.sharpe_ratio ?? null) },
    { label: "最大回撤", value: fmtPct(metrics.max_drawdown ?? null) },
    { label: "单次调仓平均换手", value: fmtPct(metrics.avg_turnover_per_rebalance ?? null, 2, false) },
  ];
});

onMounted(async () => {
  try {
    const result = await fetchV2Strategies();
    strategiesEnvelope.value = result.envelope;
    if (!researchForm.strategy_id && strategies.value.length) {
      researchForm.strategy_id = strategies.value[0]!.strategy_id;
    }
    if (!packetForm.strategy_id && strategies.value.length) {
      packetForm.strategy_id = strategies.value[0]!.strategy_id;
    }
    // 自动关联策略工厂产生的最近真实回测（用户手动填写优先；策略与回测保持一致）
    const last = wealthLabV2.lastStrategyBacktest;
    if (last) {
      if (!packetForm.backtest_id.trim()) packetForm.backtest_id = last.backtestId;
      packetForm.strategy_id = last.strategyId;
      if (!checkForm.backtest_id.trim()) checkForm.backtest_id = last.backtestId;
    }
  } catch (err) {
    baseError.value = errorMessage(err);
  }
});
</script>

<template>
  <div class="ai-risk-view">
    <div class="page-head">
      <p class="page-sub head-sub">
        AI 输出恒为研究辅助，不修改硬风险规则结果；风险否决（VETO）由确定性程序执行，被削减权重保留为现金，不自动重分配
      </p>
      <div class="head-chips">
        <span v-if="researchSource === 'offline-mock'" class="badge warn">契约 Mock 样例</span>
        <span v-else-if="research?.data.data_mode === 'REAL'" class="badge ok">真实数据</span>
        <span class="badge action">契约 wealth-lab/v2</span>
      </div>
    </div>

    <div v-if="baseError" class="error-box">{{ baseError }}</div>

    <div class="flow-strip" aria-label="操作流程">
      <template v-for="(step, index) in flowSteps" :key="step.label">
        <span class="flow-step" :class="{ done: step.done }">
          <i>{{ step.done ? "✓" : index + 1 }}</i>{{ step.label }}
        </span>
        <span v-if="index < flowSteps.length - 1" class="flow-arrow">→</span>
      </template>
      <span class="flow-note">按顺序完成；第 3 步起每步结果自动带入下一步</span>
    </div>

    <div class="top-grid">
      <!-- AI 多资产研究 -->
      <section class="panel" aria-labelledby="wl2-ai-title">
        <h3 id="wl2-ai-title">
          AI 多资产研究
          <span class="h3-extra">真实模型调用 · 独立审计留痕</span>
          <span class="badge warn">AI 仅研究辅助</span>
          <span v-if="research" class="badge ok">研究已完成</span>
        </h3>
        <div class="inline-form">
          <label class="field"><span>研究策略</span>
            <select v-model="researchForm.strategy_id">
              <option v-for="meta in strategies" :key="meta.strategy_id" :value="meta.strategy_id">{{ meta.name }}</option>
            </select>
          </label>
          <label class="field"><span>关联回测（可选）</span><input v-model="researchForm.backtest_id" type="text" placeholder="填入回测编号以引用其结果" /></label>
          <label class="field grow"><span>补充研究关注点（可选）</span><input v-model="researchForm.context" type="text" placeholder="例如：动量策略在震荡市中的表现" /></label>
          <button class="mini primary" type="button" :disabled="researchLoading" @click="runResearch">{{ researchLoading ? "研究中…" : "运行 AI 研究" }}</button>
        </div>
        <div v-if="researchError" class="error-box">{{ researchError }}</div>

        <template v-if="research">
          <p class="summary">{{ research.data.summary }}</p>
          <div class="research-grid">
            <div class="r-block"><h4>策略差异分析</h4><ul><li v-for="item in research.data.strategy_differences" :key="item">{{ item }}</li></ul></div>
            <div class="r-block warn"><h4>风险发现</h4><ul><li v-for="item in research.data.key_risks" :key="item">{{ item }}</li></ul></div>
            <div class="r-block"><h4>反面证据（与结论相反的数据）</h4><ul><li v-for="item in research.data.counter_evidence" :key="item">{{ item }}</li></ul></div>
            <div class="r-block"><h4>数据缺口与所需复核动作</h4><ul><li v-for="item in research.data.data_gaps" :key="item">{{ item }}</li></ul></div>
          </div>
          <details class="audit">
            <summary>技术审计信息（模型 / 提示词版本 / 研究编号 / 模型调用记录）</summary>
            <div class="audit-body">
              <span class="badge">模型 {{ research.data.model }}</span>
              <span class="badge">提示词 {{ research.data.prompt_version }}</span>
              <span class="badge">研究编号 {{ research.data.research_id }}</span>
              <span class="badge">引用回测 {{ research.data.used_backtest_id }}</span>
              <span v-for="warning in research.warnings" :key="warning" class="badge warn">{{ warning }}</span>
              <span v-for="callId in research.data.model_call_ids" :key="callId" class="badge">调用记录 {{ callId }}</span>
            </div>
          </details>
        </template>
        <div v-else-if="!researchLoading" class="empty">选择策略后运行 AI 研究（可选附回测编号与关注点）。</div>
      </section>

      <!-- AI 风险复核 -->
      <section class="panel" aria-labelledby="wl2-review-title">
        <h3 id="wl2-review-title">
          AI 风险复核
          <span class="h3-extra">核查研究结论证据、识别幻觉；不影响硬风险否决执行</span>
        </h3>
        <div class="inline-form">
          <label class="field grow"><span>研究编号（研究完成后自动带入）</span><input v-model="reviewForm.research_id" type="text" placeholder="wlrs_…" /></label>
          <button class="mini primary" type="button" :disabled="reviewLoading" @click="runReview">{{ reviewLoading ? "复核中…" : "运行复核" }}</button>
        </div>
        <p v-if="reviewClaims.length" class="dim-line">待核结论 {{ reviewClaims.length }} 条（研究摘要与风险发现自动带入）。</p>
        <div v-if="reviewError" class="error-box">{{ reviewError }}</div>

        <template v-if="review">
          <div class="verdict-row">
            <span :class="reviewVerdictCls" class="verdict-big">模型审阅 · {{ AI_REVIEW_VERDICT_LABELS[review.data.verdict] }}</span>
            <span class="badge" :class="review.data.human_review_required ? 'warn' : 'ok'">人工复核：{{ review.data.human_review_required ? "需要" : "不需要" }}</span>
          </div>
          <p class="verdict-note">{{ reviewNote }}</p>
          <div v-if="review.data.unchecked_claims.length" class="r-block warn">
            <h4>未核实结论（需补充证据）</h4>
            <ul><li v-for="item in review.data.unchecked_claims" :key="item">{{ item }}</li></ul>
          </div>
          <div v-if="review.data.hallucination_flags.length" class="r-block veto">
            <h4>幻觉标记（结论与数据不符）</h4>
            <ul><li v-for="item in review.data.hallucination_flags" :key="item">{{ item }}</li></ul>
          </div>
          <div class="r-block">
            <h4>复核结论说明</h4>
            <p class="dim-line">{{ review.data.notes }}</p>
          </div>
          <details class="audit">
            <summary>技术审计信息（复核编号 / 模型）</summary>
            <div class="audit-body">
              <span class="badge">复核编号 {{ review.data.review_id }}</span>
              <span class="badge">模型 {{ review.data.model }}</span>
            </div>
          </details>
        </template>
        <div v-else-if="!reviewLoading" class="empty">运行 AI 研究后可一键复核其结论证据。</div>
      </section>
    </div>

    <!-- 独立风险核查 -->
    <section class="panel" aria-labelledby="wl2-check-title">
      <h3 id="wl2-check-title">
        独立风险核查（VETO 硬规则）
        <span class="h3-extra">数据 / 资产 / 组合 / AI 结论四类检查 · 与 AI 结论相互独立</span>
        <span class="badge action">确定性数值核验</span>
        <span v-if="riskCheck" :class="RISK_VERDICT_CLS[riskCheck.data.verdict]" class="verdict-big">{{ RISK_VERDICT_LABELS[riskCheck.data.verdict] }}</span>
      </h3>
      <div class="check-grid">
        <div class="check-form">
          <h4>组合权重（小数，可超 1 以演示集中度检查）</h4>
          <label v-for="[symbol, weight] in weightEntries" :key="symbol" class="w-row">
            <span>{{ symbol }}</span>
            <input
              v-model.number="checkWeights[symbol]"
              type="number" min="0" max="1" step="0.05"
              :aria-label="`${symbol} 权重`"
            />
          </label>
          <p class="dim-line" :class="{ 'warn-text': Math.abs(weightSum - 1) > 1e-6 }">权重合计 {{ (weightSum * 100).toFixed(1) }}%</p>
          <label class="field"><span>关联回测编号（可选）</span><input v-model="checkForm.backtest_id" type="text" placeholder="wlbt_…" /></label>
          <button class="mini primary" type="button" :disabled="checkLoading" @click="runCheck">{{ checkLoading ? "核查中…" : "运行风险核查" }}</button>
        </div>

        <div class="check-results">
          <div v-if="checkError" class="error-box">{{ checkError }}</div>
          <template v-if="riskCheck">
            <div class="table-scroll">
              <table class="data check-table">
                <thead>
                  <tr><th>检查类别</th><th>规则</th><th>级别</th><th>是否触发</th><th>核查说明</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in riskCheck.data.checks" :key="`${item.category}-${item.rule}`">
                    <td>{{ RISK_CATEGORY_LABELS[item.category] ?? item.category }}</td>
                    <td>{{ riskRuleLabel(item.rule) }}</td>
                    <td><span :class="item.severity === 'VETO' ? 'badge veto' : item.severity === 'WARN' ? 'badge warn' : 'badge'">{{ RISK_SEVERITY_LABELS[item.severity] ?? item.severity }}</span></td>
                    <td><span :class="item.triggered ? 'up' : 'down'">{{ item.triggered ? "已触发" : "未触发" }}</span></td>
                    <td class="dim-cell">{{ item.detail }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <details class="audit">
              <summary>规则代码原值（技术审计）</summary>
              <div class="audit-body">
                <span v-for="item in riskCheck.data.checks" :key="item.rule" class="badge"><code>{{ item.rule }}</code></span>
              </div>
            </details>
            <div class="adjusted-row">
              <div class="r-block">
                <h4>调整后权重（削减部分保留为现金，不自动重分配）</h4>
                <div class="w-chips">
                  <span v-for="(weight, symbol) in riskCheck.data.adjusted_weights" :key="symbol" class="w-chip">
                    {{ symbol }} {{ fmtPct(weight, 1, false) }}
                  </span>
                  <span class="w-chip cash">现金 {{ fmtPct(riskCheck.data.cash_weight_after_adjustment, 1, false) }}</span>
                </div>
              </div>
              <div class="r-block">
                <h4>风控策略版本</h4>
                <p class="dim-line">{{ riskCheck.data.policy_version }}</p>
                <p v-for="warning in riskCheck.warnings" :key="warning" class="warn-text">⚠ {{ warning }}</p>
              </div>
            </div>
          </template>
          <div v-else-if="!checkLoading" class="empty">调整权重后运行核查；风险警告及以上结果将随决策包归档。</div>
        </div>
      </div>
    </section>

    <!-- 决策包 -->
    <section class="panel" aria-labelledby="wl2-packet-title">
      <h3 id="wl2-packet-title">
        独立研究决策包
        <span class="h3-extra">独立持久化 · 不可变版本 · 内容哈希校验 · 仅研究不执行</span>
      </h3>
      <div class="packet-form">
        <label class="field"><span>策略</span>
          <select v-model="packetForm.strategy_id">
            <option v-for="meta in strategies" :key="meta.strategy_id" :value="meta.strategy_id">{{ meta.name }}</option>
          </select>
        </label>
        <label class="field"><span>关联回测编号（可选）</span><input v-model="packetForm.backtest_id" type="text" placeholder="wlbt_…，填入后归档回测指标" /></label>
        <label class="field"><span>关联研究编号（可选）</span><input v-model="packetForm.research_id" type="text" placeholder="wlrs_…" /></label>
        <button class="mini primary" type="button" :disabled="packetLoading" @click="createPacket">{{ packetLoading ? "生成中…" : "生成决策包" }}</button>
        <span v-if="wealthLabV2.backtestLinkHint" class="dim-line link-hint">{{ wealthLabV2.backtestLinkHint }}</span>
        <span class="dim-line">随包归档权重：{{ Object.entries(packetWeights).map(([s, w]) => `${s} ${(w * 100).toFixed(0)}%`).join(" · ") }}</span>
      </div>
      <div v-if="packetError" class="error-box">{{ packetError }}</div>

      <template v-if="packet">
        <div class="packet-grid">
          <div class="packet-head-card">
            <span class="packet-id">{{ packet.data.packet_id }}</span>
            <span class="badge action">第 {{ packet.data.version }} 版<template v-if="packet.data.supersedes_version !== null"> · 替代第 {{ packet.data.supersedes_version }} 版</template></span>
            <span class="badge">{{ strategies.find((s) => s.strategy_id === packet.data.strategy_id)?.name ?? packet.data.strategy_id }}</span>
            <span class="badge">{{ packet.data.portfolio_id }}</span>
          </div>
          <div class="metric-block">
            <template v-if="hasPacketMetrics">
              <div class="metric-grid">
                <div v-for="item in packetMetricItems" :key="item.label" class="m-item">
                  <span>{{ item.label }}</span><b>{{ item.value }}</b>
                </div>
              </div>
            </template>
            <div v-else class="empty metrics-empty">
              尚未关联回测结果：决策包已生成并归档权重，但回测指标为空。在上方填入回测编号（wlbt_…）后重新生成，即可归档回测指标。
            </div>
          </div>
        </div>
        <div class="packet-foot">
          <div class="r-block">
            <h4>证据链（回测 / 研究 / 复核）</h4>
            <div class="w-chips">
              <span v-for="id in packet.data.evidence_ids" :key="id" class="w-chip">回测证据 {{ id }}</span>
              <span v-if="packet.data.ai_research && 'research_id' in packet.data.ai_research" class="w-chip">AI 研究 {{ (packet.data.ai_research as Record<string, string>).research_id }}</span>
              <span v-if="packet.data.ai_risk_review && 'review_id' in packet.data.ai_risk_review" class="w-chip">AI 复核 {{ (packet.data.ai_risk_review as Record<string, string>).review_id }}</span>
            </div>
            <p class="dim-line">数据截止 {{ packet.data.data_cutoff }} · 生成时间 {{ packet.data.created_at }} · 成本模型：按换手幅度 × 单边费率（bps）计提，人民币计价</p>
          </div>
          <details class="audit">
            <summary>技术审计信息（内容哈希 / 数据来源 / 模型调用记录）</summary>
            <div class="audit-body vertical">
              <p class="hash-line"><code>{{ packet.data.content_hash }}</code></p>
              <p class="dim-line">数据来源：{{ packet.data.data_sources.join(" + ") }}</p>
              <p class="dim-line">模型调用：{{ packet.data.model_call_ids.join("、") || "无" }}</p>
            </div>
          </details>
        </div>
      </template>
      <div v-else-if="!packetLoading" class="empty">生成决策包后，权重、回测窗口、风险核查与 AI 证据链将被不可变归档。</div>
    </section>
  </div>
</template>

<style scoped>
.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.head-sub { margin: 0; font-size: 13px; }
.head-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.top-grid { display: grid; grid-template-columns: 1.25fr 1fr; gap: 16px; margin-bottom: 16px; align-items: start; }
.inline-form { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 12px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-dim); }
.field input, .field select { min-width: 150px; }
.field.grow { flex: 1; min-width: 200px; }
button.primary { border-color: var(--accent); color: var(--accent); }
button.primary:hover:not(:disabled) { background: rgba(77, 163, 255, 0.12); }
.summary { margin: 8px 0 12px; font-size: 13.5px; line-height: 1.85; color: var(--text); border-left: 3px solid var(--accent); padding-left: 12px; }
.research-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.r-block { border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; background: rgba(10, 14, 20, 0.5); }
.r-block.warn { border-color: rgba(245, 166, 35, 0.4); }
.r-block.veto { border-color: rgba(255, 84, 100, 0.5); }
.r-block h4 { margin: 0 0 8px; font-size: 12.5px; color: var(--text-dim); font-weight: 600; }
.r-block ul { margin: 0; padding-left: 16px; display: flex; flex-direction: column; gap: 6px; }
.r-block li { font-size: 12.5px; color: var(--text); line-height: 1.65; }
.verdict-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.verdict-big { font-size: 14px; font-weight: 700; padding: 4px 14px; border-radius: 12px; }
.dim-line { font-size: 12.5px; color: var(--text-dim); margin: 6px 0 0; }
.link-hint { color: var(--accent-2); }
.verdict-note { margin: 8px 0 10px; font-size: 12.5px; color: var(--text); line-height: 1.7; border-left: 3px solid rgba(38, 49, 64, 0.9); padding-left: 10px; }
.flow-strip {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  margin-bottom: 16px; padding: 10px 14px;
  border: 1px solid rgba(77, 163, 255, 0.3); border-radius: 10px;
  background: rgba(77, 163, 255, 0.05);
}
.flow-step {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 4px 12px; border-radius: 14px;
  border: 1px solid var(--border); color: var(--text-dim); font-size: 12.5px;
}
.flow-step i {
  font-style: normal; display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 50%;
  background: rgba(38, 49, 64, 0.6); font-size: 11px;
}
.flow-step.done { color: var(--text); border-color: rgba(0, 196, 140, 0.45); }
.flow-step.done i { background: rgba(0, 196, 140, 0.18); color: var(--down); font-weight: 700; }
.flow-arrow { color: var(--text-dim); }
.flow-note { margin-left: auto; font-size: 11.5px; color: var(--text-dim); }
.warn-text { color: var(--warn); }
.audit { margin-top: 12px; border: 1px dashed rgba(38, 49, 64, 0.9); border-radius: 10px; padding: 8px 12px; }
.audit summary { cursor: pointer; color: var(--text-dim); font-size: 12px; user-select: none; }
.audit summary:hover { color: var(--accent); }
.audit-body { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.audit-body.vertical { flex-direction: column; }
.hash-line { margin: 4px 0; }
.hash-line code { font-size: 11.5px; color: var(--accent-2); word-break: break-all; }
.check-grid { display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start; }
.check-form { display: flex; flex-direction: column; gap: 11px; border: 1px solid var(--border); border-radius: 10px; padding: 13px 15px; background: rgba(10, 14, 20, 0.5); }
.check-form h4 { margin: 0; font-size: 12.5px; color: var(--text-dim); }
.w-row { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.w-row span { width: 90px; color: var(--text); }
.w-row input { width: 92px; text-align: right; font-variant-numeric: tabular-nums; }
.check-results { display: flex; flex-direction: column; gap: 12px; }
.check-table th, .check-table td { font-size: 13px; padding: 10px 12px; }
.adjusted-row { display: grid; grid-template-columns: 1.2fr 1fr; gap: 12px; }
.w-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.w-chip { display: inline-block; padding: 3px 10px; border: 1px solid var(--border); border-radius: 9px; font-size: 12.5px; color: var(--text); background: rgba(38, 49, 64, 0.4); font-variant-numeric: tabular-nums; }
.w-chip.cash { border-color: rgba(232, 180, 90, 0.5); color: var(--gold); }
.packet-form { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 14px; }
.packet-grid { display: grid; grid-template-columns: 340px 1fr; gap: 14px; align-items: stretch; }
.packet-head-card { display: flex; flex-direction: column; gap: 9px; border: 1px solid rgba(34, 211, 238, 0.4); border-radius: 12px; padding: 14px 16px; background: rgba(10, 14, 20, 0.5); }
.packet-id { font-size: 18px; font-weight: 700; color: var(--accent-2); font-variant-numeric: tabular-nums; }
.metric-block { display: flex; }
.metric-grid { flex: 1; display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px 16px; border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.metrics-empty { flex: 1; }
.m-item { display: flex; flex-direction: column; gap: 3px; }
.m-item span { font-size: 12px; color: var(--text-dim); }
.m-item b { font-size: 17px; font-variant-numeric: tabular-nums; }
.packet-foot { display: grid; grid-template-columns: 1.2fr 1fr; gap: 12px; margin-top: 12px; }
.dim-cell { color: var(--text-dim); font-size: 12.5px; }
@media (max-width: 1280px) {
  .top-grid, .check-grid, .adjusted-row, .packet-grid, .packet-foot { grid-template-columns: 1fr; }
}
</style>
