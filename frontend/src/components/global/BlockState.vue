<script setup lang="ts">
import { computed } from "vue";
import { getBlockReasonLabel } from "../../domain/labels";

const props = defineProps<{
  block: { available: boolean; reason_code: string | null; stale?: boolean } | undefined;
}>();

const reason = computed(() => getBlockReasonLabel(props.block?.reason_code ?? "not_found"));
</script>

<template>
  <div v-if="!block || !block.available" class="block-state">
    <slot name="fallback"><span class="dim">{{ reason }}</span></slot>
  </div>
  <div v-else-if="block.stale" class="block-state">
    <slot />
    <span class="warn-text">数据过期</span>
  </div>
  <div v-else>
    <slot />
  </div>
</template>

<style scoped>
.block-state {
  padding: 8px 0;
}
.dim {
  color: var(--text-dim);
}
.warn-text {
  color: var(--warn);
  margin-left: 8px;
}
</style>
