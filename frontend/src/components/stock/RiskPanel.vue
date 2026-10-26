<script setup lang="ts">
import { computed } from "vue";
import { getRiskDetail, getRiskLabel } from "../../domain/labels";

const props = defineProps<{ risks: { code: string; value: unknown }[]; reason?: string | null; researchTime?: string | null }>();

const items = computed(() =>
  props.risks.length
    ? props.risks.map((risk) => ({ ...risk,
      text: risk.code === "research_finding" ? (getRiskDetail(risk.code, risk.value) ?? getRiskLabel(risk.code)) : getRiskLabel(risk.code),
      detail: risk.code === "research_finding" ? null : getRiskDetail(risk.code, risk.value),
    }))
    : [{ code: "NO_RISK", value: null, text: getRiskLabel("NO_RISK"), detail: null }]
);
</script>

<template>
  <section class="panel">
    <h3>{{ researchTime ? '研究风险提示' : '风险标记' }}</h3>
    <p v-if="reason" class="dim">{{ reason }}</p>
    <ul v-else class="risk-list">
      <li v-for="(item, index) in items" :key="`${item.code}-${index}`" :class="{ warn: item.code !== 'NO_RISK' }">
        {{ item.text }}
        <small v-if="item.detail">{{ item.detail }}</small>
      </li>
    </ul>
    <p v-if="researchTime" class="risk-scope">来源：本轮研究记录。研究提示不替代冻结决策的 VETO 复核。</p>
  </section>
</template>

<style scoped>
.risk-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.risk-list li.warn {
  color: var(--warn);
}
.risk-list small {
  display: block;
  margin-top: 5px;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
}
.risk-scope { margin: 12px 0 0; color: var(--text-dim); font-size: 11px; line-height: 1.6; }
</style>
