<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { errorMessage } from "../api/client";
import type { CandidatesPayload, RunManifest, StateDefinitions, TransitionRow } from "../api/capitalBehavior";
import { fetchCandidates, fetchManifest, fetchStates, fetchTransitions } from "../api/capitalBehavior";
import CandidateComparison from "../components/capital/CandidateComparison.vue";
import TransitionHeatmap from "../components/capital/TransitionHeatmap.vue";
import TransitionSankey from "../components/capital/TransitionSankey.vue";
import { getCapitalBehaviorLabel, getGateStatusLabel } from "../domain/labels";

const loading = ref(false);
const error = ref<string | null>(null);
const manifest = ref<RunManifest | null>(null);
const definitions = ref<StateDefinitions | null>(null);
const transitions = ref<TransitionRow[]>([]);
const candidates = ref<CandidatesPayload | null>(null);

onMounted(async () => {
  loading.value = true;
  error.value = null;
  try {
    const [manifestData, statesData, transitionTable, candidatesData] = await Promise.all([
      fetchManifest(),
      fetchStates(),
      fetchTransitions(),
      fetchCandidates(),
    ]);
    manifest.value = manifestData;
    definitions.value = statesData;
    transitions.value = transitionTable.rows;
    candidates.value = candidatesData;
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
});

const stateNames = computed(() =>
  definitions.value ? Object.keys(definitions.value.states) : [],
);

const lifecycleChain = computed(() => {
  if (!definitions.value) return [];
  const edges = definitions.value.lifecycle_hypotheses;
  if (!edges.length) return [];
  const next = new Map(edges);
  const starts = edges.map(([from]) => from).filter((from) => !edges.some(([, to]) => to === from));
  const chain: string[] = starts.length ? [starts[0]] : [edges[0][0]];
  while (next.has(chain[chain.length - 1])) {
    const following = next.get(chain[chain.length - 1]);
    if (!following || chain.includes(following)) break;
    chain.push(following);
  }
  return chain;
});

function stateBadgeClass(name: string): string {
  if (!definitions.value) return "";
  if (definitions.value.risk_states.includes(name)) return "veto";
  if (definitions.value.alpha_states.includes(name)) return "ok";
  return "";
}

const maxAbsWeight = computed(() => {
  if (!definitions.value) return 1;
  let max = 0.01;
  for (const state of Object.values(definitions.value.states)) {
    for (const weight of Object.values(state.weights)) {
      max = Math.max(max, Math.abs(weight));
    }
  }
  return max;
});

function weightBarStyle(weight: number): Record<string, string> {
  const width = `${(Math.abs(weight) / maxAbsWeight.value) * 48}%`;
  return weight >= 0
    ? { left: "50%", width, background: "var(--accent)" }
    : { right: "50%", width, background: "var(--up)" };
}
</script>

<template>
  <h1 class="page-title">资金行为研究</h1>
  <p class="page-sub">
    可观测市场行为假设（非内部意图断言）· 12 类市场状态 · 加权证据 · 仅供研究
  </p>

  <div v-if="error" class="error-box">{{ error }}</div>
  <div v-else-if="loading" class="loading">加载中…</div>
  <template v-else-if="manifest && definitions && candidates">
    <div class="panel">
      <div class="manifest-head">
        <div class="stat">
          <div class="stat-value">{{ manifest.counts.packets.toLocaleString() }}</div>
          <div class="stat-label">行为数据包</div>
        </div>
        <div class="stat">
          <div class="stat-value">{{ manifest.counts.states }}</div>
          <div class="stat-label">行为状态</div>
        </div>
        <div class="stat">
          <div class="stat-value">{{ manifest.counts.strategies }}</div>
          <div class="stat-label">策略回测</div>
        </div>
        <div class="stat">
          <div class="stat-value">{{ manifest.counts.similarity_queries.toLocaleString() }}</div>
          <div class="stat-label">相似度查询</div>
        </div>
        <div class="stat">
          <div class="stat-value">{{ manifest.data_range.start }} → {{ manifest.data_range.end }}</div>
          <div class="stat-label">数据区间</div>
        </div>
      </div>
      <div class="manifest-badges">
        <span class="badge" :class="manifest.decision === 'NO_PRODUCTION_CANDIDATE' ? 'veto' : 'ok'">
          {{ getGateStatusLabel(manifest.decision) }}
        </span>
        <span v-if="manifest.research_only" class="badge">仅限研究</span>
        <span class="badge">运行时间 {{ manifest.run_at.slice(0, 16).replace("T", " ") }}</span>
      </div>
    </div>

    <CandidateComparison :payload="candidates" />

    <TransitionHeatmap :rows="transitions" :definitions="definitions" />
    <TransitionSankey :rows="transitions" />

    <div class="panel">
      <h3>生命周期假设链</h3>
      <div class="lifecycle-chain">
        <template v-for="(name, index) in lifecycleChain" :key="name">
          <span class="badge" :class="stateBadgeClass(name)">{{ getCapitalBehaviorLabel(name) }}</span>
          <span v-if="index < lifecycleChain.length - 1" class="chain-arrow">→</span>
        </template>
      </div>
    </div>

    <div class="panel">
      <h3>状态定义与证据权重（{{ stateNames.length }} 个状态）</h3>
      <div class="state-grid">
        <div v-for="(state, name) in definitions.states" :key="name" class="state-card">
          <div class="state-head">
            <span class="badge" :class="stateBadgeClass(name)">{{ getCapitalBehaviorLabel(name) }}（{{ name }}）</span>
            <span class="state-intercept">截距 {{ state.intercept.toFixed(2) }}</span>
          </div>
          <p class="state-desc">{{ state.description }}</p>
          <div v-if="Object.keys(state.weights).length" class="weight-list">
            <div
              v-for="(weight, feature) in state.weights"
              :key="feature"
              class="weight-row"
              :title="`${feature}: ${weight}`"
            >
              <span class="weight-name">{{ feature }}</span>
              <div class="weight-bar">
                <span class="weight-fill" :style="weightBarStyle(weight)" />
              </div>
              <span class="weight-value">{{ weight.toFixed(2) }}</span>
            </div>
          </div>
          <div v-else class="state-desc" style="opacity: 0.6">无先验证据权重（低质量证据兜底状态）</div>
        </div>
      </div>
    </div>
  </template>
</template>

<style scoped>
.manifest-head {
  display: flex;
  flex-wrap: wrap;
  gap: 28px;
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: var(--text);
}

.stat-label {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: 2px;
}

.manifest-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.lifecycle-chain {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.chain-arrow {
  color: var(--text-dim);
}

.state-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 12px;
}

.state-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: grid;
  gap: 8px;
}

.state-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.state-intercept {
  font-size: 11px;
  color: var(--text-dim);
}

.state-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.5;
}

.weight-list {
  display: grid;
  gap: 4px;
}

.weight-row {
  display: grid;
  grid-template-columns: 150px 1fr 40px;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}

.weight-name {
  color: var(--text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.weight-bar {
  position: relative;
  height: 8px;
  background: var(--border);
  border-radius: 4px;
}

.weight-fill {
  position: absolute;
  top: 0;
  height: 100%;
  border-radius: 4px;
}

.weight-value {
  text-align: right;
  color: var(--text-dim);
}
</style>
