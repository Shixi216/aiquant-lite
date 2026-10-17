<script setup lang="ts">
import { onMounted, ref } from "vue";

import axios from "axios";

import { errorMessage, isRouterDown } from "../api/client";
import type { DailyReview, DecisionPacket, TradePlan } from "../api/decisions";
import { fetchDailyReview, fetchDecisionVersions, fetchTradePlans } from "../api/decisions";
import {
  getDecisionLabel,
  getDecisionStatusLabel,
  getHoldingHorizonLabel,
  getRiskLabel,
  getVetoStatusLabel,
} from "../domain/labels";

const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
const reviewDate = ref(today);
const review = ref<DailyReview | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const versionChain = ref<DecisionPacket[] | null>(null);
const versionError = ref<string | null>(null);

const planUserId = ref(localStorage.getItem("webui_plan_user_id") ?? "");
const planStatus = ref("");
const plans = ref<TradePlan[] | null>(null);
const plansError = ref<string | null>(null);
const plansLoading = ref(false);

const PLAN_STATUSES = [
  ["", "全部"],
  ["DRAFT", "草稿"],
  ["PENDING_CONFIRMATION", "待确认"],
  ["CONFIRMED", "已确认"],
  ["ACTIVE", "生效中"],
  ["PARTIALLY_EXECUTED", "部分执行"],
  ["COMPLETED", "已完成"],
  ["CANCELLED", "已取消"],
  ["EXPIRED", "已过期"],
] as const;

function actionClass(action: string | undefined): string {
  if (!action) return "";
  if (["STRONG_BUY", "BUY", "SMALL_BUY", "ADD"].includes(action)) return "up";
  if (["REDUCE", "SELL", "EXIT", "CLEAR"].includes(action)) return "down";
  return "";
}

async function guarded(fn: () => Promise<void>) {
  loading.value = true;
  error.value = null;
  try {
    await fn();
  } catch (err) {
    error.value = isRouterDown(err) ? "Router(8765) 不可达" : errorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function loadReview() {
  await guarded(async () => {
    review.value = await fetchDailyReview(reviewDate.value);
    versionChain.value = null;
  });
}

async function openVersions(packet: DecisionPacket) {
  versionError.value = null;
  versionChain.value = null;
  try {
    versionChain.value = await fetchDecisionVersions(packet.decision_id);
  } catch (err) {
    versionError.value = errorMessage(err);
  }
}

const plansEndpointMissing = ref(false);

async function loadPlans() {
  if (!planUserId.value.trim()) {
    plansError.value = "请输入 local_user_id（创建交易计划时使用的用户标识）";
    plans.value = null;
    return;
  }
  localStorage.setItem("webui_plan_user_id", planUserId.value.trim());
  plansLoading.value = true;
  plansError.value = null;
  plansEndpointMissing.value = false;
  try {
    plans.value = await fetchTradePlans({
      local_user_id: planUserId.value.trim(),
      status: planStatus.value || undefined,
    });
  } catch (err) {
    plans.value = null;
    plansEndpointMissing.value = axios.isAxiosError(err) && err.response?.status === 404;
    if (!plansEndpointMissing.value) {
      plansError.value = isRouterDown(err) ? "Router(8765) 不可达" : errorMessage(err);
    }
  } finally {
    plansLoading.value = false;
  }
}

onMounted(loadReview);
</script>

<template>
  <h1 class="page-title">决策卡片与交易计划</h1>
  <p class="page-sub">
    按日复盘 DecisionPacket · 不可变版本链 · 交易计划（只读）·
    日期为北京时间
  </p>

  <div class="toolbar">
    <input v-model="reviewDate" type="date" @change="loadReview" />
    <span v-if="review" class="badge">风控否决 {{ review.veto_count }}</span>
    <span v-if="review" class="badge">决策包 {{ review.decision_packets.length }}</span>
    <button class="mini" :disabled="loading" @click="loadReview">刷新</button>
  </div>

  <div v-if="error" class="error-box">{{ error }}</div>
  <div v-else-if="loading" class="loading">加载中…</div>
  <template v-else-if="review">
    <div v-if="!review.decision_packets.length" class="panel">
      <div class="empty">
        {{ reviewDate }} 无决策包。
        <template v-if="review.summary.length">
          <ul style="text-align: left; margin: 12px auto; max-width: 640px">
            <li v-for="line in review.summary" :key="line">{{ line }}</li>
          </ul>
        </template>
      </div>
    </div>

    <div v-else class="decision-grid">
      <button
        v-for="packet in review.decision_packets"
        :key="packet.decision_id"
        class="panel decision-card"
        :title="'查看版本链'"
        @click="openVersions(packet)"
      >
        <div class="decision-head">
          <strong>{{ packet.symbol }}</strong>
          <span class="badge action">v{{ packet.decision_version }}</span>
          <span class="badge">{{ getDecisionStatusLabel(packet.status) }}</span>
        </div>
        <div class="decision-action">
          <span class="action-label" :class="actionClass(packet.decision.final_action)">
            {{ getDecisionLabel(packet.decision.final_action) }}
          </span>
          <span class="weight">目标权重 {{ (packet.decision.final_target_weight * 100).toFixed(1) }}%</span>
        </div>
        <div class="decision-scores">
          <span>置信度 {{ (packet.confidence * 100).toFixed(0) }}%</span>
          <span>证据质量 {{ (packet.evidence_quality * 100).toFixed(0) }}%</span>
          <span>仓位上限 {{ (packet.suggested_position_limit * 100).toFixed(0) }}%</span>
          <span>持有 {{ getHoldingHorizonLabel(packet.holding_horizon) }}</span>
        </div>
        <div v-if="packet.risk_flags.length" class="decision-flags">
          <span v-for="flag in packet.risk_flags" :key="flag" class="badge veto">{{ getRiskLabel(flag) }}</span>
        </div>
        <div class="decision-cases">
          <div><span class="case-tag up">多</span>{{ packet.bull_case.summary }}</div>
          <div><span class="case-tag">基</span>{{ packet.base_case.summary }}</div>
          <div><span class="case-tag down">空</span>{{ packet.bear_case.summary }}</div>
        </div>
        <div v-if="packet.decision.rationale_summary.length" class="decision-rationale">
          {{ packet.decision.rationale_summary[0] }}
        </div>
      </button>
    </div>
  </template>

  <div class="panel">
    <h3>交易计划（只读）</h3>
    <div class="toolbar" style="margin-bottom: 10px">
      <input
        v-model="planUserId"
        type="text"
        placeholder="本地用户ID (local_user_id)"
        style="width: 220px"
        @keyup.enter="loadPlans"
      />
      <select v-model="planStatus" @change="loadPlans">
        <option v-for="[value, label] in PLAN_STATUSES" :key="value" :value="value">
          {{ label }}
        </option>
      </select>
      <button class="mini" :disabled="plansLoading" @click="loadPlans">查询</button>
      <span v-if="plans" class="badge">{{ plans.length }} 个计划</span>
    </div>
    <div v-if="plansEndpointMissing" class="error-box" style="padding: 14px">
      trade-plans 端点尚未在运行中的 Router 上生效（代码已就绪）。
      重启 Router(:8765) 后此面板即可使用。
    </div>
    <div v-else-if="plansError" class="error-box" style="padding: 14px">{{ plansError }}</div>
    <table v-else-if="plans && plans.length" class="data">
      <thead>
        <tr>
          <th>代码</th>
          <th>名称</th>
          <th>动作</th>
          <th class="num">现仓位</th>
          <th class="num">建议仓位</th>
          <th class="num">现价</th>
          <th>建仓区间</th>
          <th class="num">止损</th>
          <th class="num">盈亏比</th>
          <th class="num">置信度</th>
          <th>状态</th>
          <th>风控否决</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="plan in plans" :key="plan.plan_id">
          <td>{{ plan.symbol }}</td>
          <td>{{ plan.stock_name || "—" }}</td>
          <td :class="actionClass(plan.action)">{{ getDecisionLabel(plan.action) }}</td>
          <td class="num">{{ (plan.current_position * 100).toFixed(1) }}%</td>
          <td class="num">{{ (plan.recommended_position * 100).toFixed(1) }}%</td>
          <td class="num">{{ plan.current_price }}</td>
          <td>{{ plan.entry_zone.length ? plan.entry_zone.join(" ~ ") : "—" }}</td>
          <td class="num down">{{ plan.stop_loss_price ?? "—" }}</td>
          <td class="num">{{ plan.risk_reward_ratio.toFixed(2) }}</td>
          <td class="num">{{ (plan.confidence * 100).toFixed(0) }}%</td>
          <td><span class="badge">{{ plan.status_zh }}</span></td>
          <td>
            <span class="badge" :class="plan.veto_status === 'NO_VETO' ? 'ok' : 'veto'">
              {{ getVetoStatusLabel(plan.veto_status) }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else-if="plans" class="empty">该用户暂无交易计划。</div>
    <div v-else class="empty">输入 local_user_id 后查询。</div>
  </div>

  <div v-if="versionChain || versionError" class="modal-mask" @click.self="versionChain = null; versionError = null">
    <div class="modal">
      <div class="modal-head">
        <h3 style="margin: 0">决策版本链</h3>
        <button class="close-btn" @click="versionChain = null; versionError = null">✕</button>
      </div>
      <div v-if="versionError" class="error-box">{{ versionError }}</div>
      <template v-else-if="versionChain">
        <div v-for="version in versionChain" :key="version.decision_version" class="version-row">
          <span class="badge action">v{{ version.decision_version }}</span>
          <span v-if="version.supersedes_version" class="badge">
            替代 v{{ version.supersedes_version }}
          </span>
          <span class="badge">{{ version.generated_at.replace("T", " ").slice(0, 19) }}</span>
          <span class="action-label" :class="actionClass(version.decision.final_action)">
            {{ getDecisionLabel(version.decision.final_action) }}
          </span>
          <span class="weight">权重 {{ (version.decision.final_target_weight * 100).toFixed(1) }}%</span>
          <span class="badge" :class="version.status === 'SUPERSEDED' ? '' : 'ok'">
            {{ getDecisionStatusLabel(version.status) }}
          </span>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.decision-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 12px;
}

.decision-card {
  text-align: left;
  cursor: pointer;
  margin-bottom: 0;
  display: grid;
  gap: 8px;
  color: inherit;
  font: inherit;
}

.decision-head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.decision-action {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.action-label {
  font-size: 18px;
  font-weight: 700;
}

.weight {
  color: var(--text-dim);
  font-size: 12px;
}

.decision-scores {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--text-dim);
}

.decision-flags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.decision-cases {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--text-dim);
}

.case-tag {
  display: inline-block;
  width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  border-radius: 4px;
  border: 1px solid var(--border);
  margin-right: 6px;
  font-size: 11px;
}

.decision-rationale {
  font-size: 12px;
  color: var(--text-dim);
  border-top: 1px solid var(--border);
  padding-top: 8px;
}

.version-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
</style>
