<script setup lang="ts">
import { ref } from "vue";

defineProps<{ payload: unknown }>();

const open = ref(false);
</script>

<template>
  <details class="raw-json" @toggle="open = ($event.target as HTMLDetailsElement).open">
    <summary>原始 JSON</summary>
    <pre>{{ JSON.stringify(payload, null, 2) }}</pre>
  </details>
</template>

<style scoped>
.raw-json {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-dim);
}
.raw-json pre {
  max-height: 320px;
  overflow: auto;
  background: var(--bg);
  border: 1px solid var(--border);
  padding: 8px;
}
</style>
