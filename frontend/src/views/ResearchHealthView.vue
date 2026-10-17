<script setup lang="ts">
import { ref } from "vue";

import { api, errorMessage } from "../api/client";
import HealthPanel from "../components/research/HealthPanel.vue";
import SentimentPanel from "../components/research/SentimentPanel.vue";
import StrategyLabPanel from "../components/research/StrategyLabPanel.vue";
import { usePolling } from "../composables/usePolling";

const TABS = [
  ["strategy-lab", "策略实验室"],
  ["sentiment", "情绪因子"],
  ["experiments", "实验"],
  ["health", "系统健康"],
] as const;

const activeTab = ref<(typeof TABS)[number][0]>("strategy-lab");

const experimentRunId = ref("");
const experiment = ref<unknown>(null);
const experimentError = ref<string | null>(null);
const experimentLoading = ref(false);

const healthPanel = ref<InstanceType<typeof HealthPanel> | null>(null);

async function loadExperiment() {
  if (!experimentRunId.value.trim()) return;
  experimentLoading.value = true;
  experimentError.value = null;
  experiment.value = null;
  try {
    const { data } = await api.get(`/v1/experiment-runs/${encodeURIComponent(experimentRunId.value.trim())}`);
    experiment.value = data;
  } catch (err) {
    experimentError.value = errorMessage(err);
  } finally {
    experimentLoading.value = false;
  }
}

function onPoll() {
  if (activeTab.value === "health") healthPanel.value?.refresh(true);
}

usePolling(onPoll, 60_000, 300_000);
</script>

<template>
  <h1 class="page-title">实验与系统状态</h1>
  <p class="page-sub">Strategy Lab 走查 · 情绪因子评估 · 实验运行查询 · 系统健康（交易时段 60s 轮询）</p>

  <div class="toolbar">
    <button
      v-for="[key, label] in TABS"
      :key="key"
      class="chip"
      :class="{ active: activeTab === key }"
      @click="activeTab = key"
    >
      {{ label }}
    </button>
  </div>

  <StrategyLabPanel v-show="activeTab === 'strategy-lab'" />
  <SentimentPanel v-show="activeTab === 'sentiment'" />

  <div v-show="activeTab === 'experiments'">
    <div class="panel">
      <h3>实验运行查询</h3>
      <div class="toolbar">
        <input
          v-model="experimentRunId"
          type="text"
          placeholder="run_id"
          style="width: 320px"
          @keyup.enter="loadExperiment"
        />
        <button class="mini" :disabled="experimentLoading" @click="loadExperiment">查询</button>
      </div>
      <div v-if="experimentError" class="error-box" style="padding: 14px">{{ experimentError }}</div>
      <pre v-else-if="experiment" class="json-view">{{ JSON.stringify(experiment, null, 2) }}</pre>
      <div v-else class="empty">输入 run_id 后查询（POST /v1/experiments 创建的实验或 run）。</div>
    </div>
  </div>

  <div v-show="activeTab === 'health'">
    <HealthPanel ref="healthPanel" />
  </div>
</template>

<style scoped>
.json-view {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  font-size: 12px;
  max-height: 480px;
  overflow: auto;
  color: var(--text-dim);
}
</style>
