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
  <p class="page-sub">策略实验 · 情绪因子评估 · 运行查询 · 系统健康。结果来自已有研究产物，仅供研究。</p>

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

  <StrategyLabPanel v-if="activeTab === 'strategy-lab'" />
  <SentimentPanel v-if="activeTab === 'sentiment'" />

  <div v-show="activeTab === 'experiments'">
    <div class="panel">
      <h3>实验运行查询</h3>
      <div class="toolbar">
        <input
          v-model="experimentRunId"
          type="text"
          placeholder="输入实验运行编号"
          style="width: 320px"
          @keyup.enter="loadExperiment"
        />
        <button class="mini" :disabled="experimentLoading" @click="loadExperiment">查询</button>
      </div>
      <div v-if="experimentError" class="error-box" style="padding: 14px">{{ experimentError }}</div>
      <pre v-else-if="experiment" class="json-view">{{ JSON.stringify(experiment, null, 2) }}</pre>
      <div v-else class="empty">输入已有实验的运行编号后查询；此页面只读取结果。</div>
    </div>
  </div>

  <div v-if="activeTab === 'health'">
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
