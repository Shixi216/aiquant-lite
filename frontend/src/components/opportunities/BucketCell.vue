<script setup lang="ts">
import { computed } from "vue";
import { getScannerBucketLabel } from "../../domain/labels";

const props = defineProps<{ bucket: string | null | undefined; classificationSource: string }>();

const label = computed(() => getScannerBucketLabel(props.bucket));
const unavailable = computed(() => !props.bucket);
</script>

<template>
  <span :class="['bucket', bucket ?? '', { unavailable }]" :title="unavailable ? '当前没有可用的正式扫描层级' : classificationSource">
    {{ label }}
  </span>
</template>

<style scoped>
.bucket.unavailable {
  color: var(--text-dim);
}
.bucket.CORE {
  color: var(--up);
}
.bucket.NEAR {
  color: var(--warn);
}
.hint {
  font-size: 11px;
}
</style>
