<script setup lang="ts">
import { computed } from "vue";
import { getAdjustmentLabel } from "../../domain/labels";

const props = defineProps<{
  adjustment: {
    requested_adjustment_type: string | null;
    effective_adjustment_type: string | null;
    adjustment_fallback: boolean | null;
    adjustment_fallback_reason: string | null;
  };
}>();

const label = computed(() => getAdjustmentLabel(props.adjustment.effective_adjustment_type));
const degraded = computed(() => props.adjustment.adjustment_fallback === true);
const reasonText = computed(() => {
  if (!degraded.value) return "";
  const reason = props.adjustment.adjustment_fallback_reason;
  if (reason === "missing_adjustment_factors") return "缺少复权因子，当前为不复权数据，除权日存在价格跳空";
  if (reason === "factor_window_incomplete") return "复权因子不完整，当前为不复权数据，除权日存在价格跳空";
  return "复权口径降级";
});
</script>

<template>
  <span :class="['adjustment-badge', { degraded }]">
    复权：{{ label }}
    <span v-if="degraded" class="warn">⚠ {{ reasonText }}</span>
  </span>
</template>

<style scoped>
.adjustment-badge {
  color: var(--text-dim);
  font-size: 12px;
}
.adjustment-badge.degraded {
  color: var(--warn);
}
.warn {
  margin-left: 6px;
}
</style>
