<script setup lang="ts">
import { computed } from "vue";
import { getHealthStatusLabel, getComponentLabel } from "../../domain/labels";

const props = defineProps<{
  name: string;
  status: string;
  healthy: boolean;
}>();

const label = computed(() => getHealthStatusLabel(props.status));
</script>

<template>
  <span :class="['chip', status === 'unknown' ? 'neutral' : healthy ? 'ok' : 'bad']" :title="`${name}: ${status}`">
    {{ getComponentLabel(name) }} · {{ label }}
  </span>
</template>

<style scoped>
.chip.neutral { color: var(--text-faint); background: var(--bg-hover); }
.chip {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  border: 1px solid var(--border);
  margin-right: 8px;
}
.chip.ok {
  color: var(--down);
}
.chip.bad {
  color: #fff;
  background: var(--veto);
  border-color: var(--veto);
}
</style>
