<script setup lang="ts">
import { computed } from "vue";
import { getRiskLabel } from "../../domain/labels";

const props = defineProps<{ risks: { code: string; value: unknown }[]; reason?: string | null }>();

const items = computed(() =>
  props.risks.length
    ? props.risks.map((risk) => ({ ...risk, text: getRiskLabel(risk.code) }))
    : [{ code: "NO_RISK", value: null, text: getRiskLabel("NO_RISK") }]
);
</script>

<template>
  <section class="panel">
    <h3>风险标记</h3>
    <p v-if="reason" class="dim">{{ reason }}</p>
    <ul v-else class="risk-list">
      <li v-for="item in items" :key="item.code" :class="{ warn: item.code !== 'NO_RISK' }">
        {{ item.text }}
      </li>
    </ul>
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
</style>
