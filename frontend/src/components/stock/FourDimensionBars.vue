<script setup lang="ts">
import { computed } from "vue";
import type { CrossSectionSymbolData } from "../../types/v2";
import {
  formatRank,
  formatScore,
  getScoreSourceLabel,
  LEGACY_NOTE,
  RANK_TOOLTIP,
} from "../../domain/labels";

const props = defineProps<{ data: CrossSectionSymbolData | null; reason?: string | null }>();

const dims = computed(() => {
  const data = props.data;
  if (!data) return [];
  return [
    { key: "tech", label: "技术质量", value: data.tech_rank },
    { key: "fund", label: "基本面质量", value: data.fund_rank },
    { key: "sent", label: "交易行为", value: data.sent_rank },
    { key: "margin", label: "两融表现", value: data.margin_rank },
  ];
});

const isLegacy = computed(() => props.data?.score_source === "legacy_60_40_fallback");
</script>

<template>
  <section class="panel">
    <h3>四维拆解</h3>
    <div v-if="!data" class="dim">暂无数据{{ reason ? `（${reason}）` : "" }}</div>
    <template v-else>
      <p class="composite">
        综合评分 <strong>{{ formatScore(data.composite_score) }}</strong>
        <span class="rank">综合分 / 100</span>
      </p>
      <div class="bars">
        <div v-for="dim in dims" :key="dim.key" class="bar-row">
          <span class="bar-label">{{ dim.label }}</span>
          <div class="bar-track">
            <div
              class="bar-fill"
              :style="{ width: `${dim.value == null ? 0 : Math.max(0, Math.min(100, (dim.value + 1) * 50))}%` }"
            ></div>
          </div>
          <span class="bar-value" :title="RANK_TOOLTIP">{{ formatRank(dim.value) }}</span>
        </div>
      </div>
      <p class="source" :class="{ legacy: isLegacy }">
        评分来源：{{ getScoreSourceLabel(data.score_source) }}
        <span v-if="isLegacy">（{{ LEGACY_NOTE }}）</span>
      </p>
    </template>
  </section>
</template>

<style scoped>
.composite {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.composite strong {
  font-size: 20px;
}
.rank {
  color: var(--text-dim);
  font-size: 12px;
}
.bars {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}
.bar-row {
  display: grid;
  grid-template-columns: 90px 1fr 90px;
  align-items: center;
  gap: 10px;
}
.bar-label {
  font-size: 13px;
  color: var(--text-dim);
}
.bar-track {
  height: 10px;
  border-radius: 5px;
  background: var(--bg);
  border: 1px solid var(--border);
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: var(--accent);
}
.bar-value {
  font-size: 12px;
  color: var(--text-dim);
  text-align: right;
}
.source {
  margin-top: 12px;
  font-size: 12px;
  color: var(--text-dim);
}
.source.legacy {
  color: var(--warn);
}
.dim {
  color: var(--text-dim);
}
</style>
