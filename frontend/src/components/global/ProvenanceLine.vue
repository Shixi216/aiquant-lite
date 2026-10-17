<script setup lang="ts">
import { computed } from "vue";
import {
  getDataSourceLabel,
  getFormalStatusLabel,
} from "../../domain/labels";

const props = defineProps<{
  source: string | null | undefined;
  dataTime?: string | null;
  calculationSource?: string | null;
  formalStatus?: string | null;
  extra?: string[];
}>();

const line = computed(() => {
  const parts: string[] = [];
  parts.push(`来源：${getDataSourceLabel(props.source)}`);
  if (props.dataTime) parts.push(`截止：${props.dataTime}`);
  for (const item of props.extra ?? []) parts.push(item);
  if (props.formalStatus) parts.push(`性质：${getFormalStatusLabel(props.formalStatus)}`);
  return parts.join("　");
});
</script>

<template>
  <p class="provenance-line">{{ line }}</p>
</template>

<style scoped>
.provenance-line {
  color: var(--text-dim);
  font-size: 12px;
  margin: 6px 0 0;
}
</style>
