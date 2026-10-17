<script setup lang="ts">
import { computed } from "vue";
import { formatScore, formatRank, RANK_TOOLTIP, NO_DATA } from "../../domain/labels";

const props = defineProps<{ value: number | null | undefined; rank?: boolean }>();
const display = computed(() => props.rank ? formatRank(props.value) : formatScore(props.value));
</script>

<template>
  <span class="score-cell" :title="rank ? RANK_TOOLTIP : '综合分按原始分线性换算为0—100；不是市场排名或上涨概率。'">
    {{ display }}
    <span v-if="display === NO_DATA" class="dim">—</span>
  </span>
</template>

<style scoped>
.score-cell {
  font-variant-numeric: tabular-nums;
}
.dim {
  color: var(--text-dim);
}
</style>
