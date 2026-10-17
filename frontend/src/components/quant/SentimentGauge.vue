<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ temperature: number; caption?: string }>();

const RADIUS = 78;
const CENTER = 100;
const ARC_SPAN = 180;

const clamped = computed(() => Math.max(0, Math.min(100, props.temperature)));

const arcPath = computed(() => {
  const startAngle = 180;
  const endAngle = 180 + (clamped.value / 100) * ARC_SPAN;
  const rad = (deg: number) => (deg * Math.PI) / 180;
  const x1 = CENTER + RADIUS * Math.cos(rad(startAngle));
  const y1 = CENTER + RADIUS * Math.sin(rad(startAngle));
  const x2 = CENTER + RADIUS * Math.cos(rad(endAngle));
  const y2 = CENTER + RADIUS * Math.sin(rad(endAngle));
  const largeArc = (endAngle - startAngle) > 180 ? 1 : 0;
  return `M ${x1} ${y1} A ${RADIUS} ${RADIUS} 0 ${largeArc} 1 ${x2} ${y2}`;
});

const tempColor = computed(() => {
  const t = clamped.value;
  if (t >= 70) return "var(--up)";
  if (t >= 45) return "var(--gold)";
  return "var(--down)";
});

const tempLabel = computed(() => {
  const t = clamped.value;
  if (t >= 70) return "偏热";
  if (t >= 45) return "中性";
  return "偏冷";
});
</script>

<template>
  <div class="gauge-wrap">
    <svg viewBox="0 0 200 118" class="gauge-svg">
      <defs>
        <linearGradient id="gaugeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#00c48c" />
          <stop offset="50%" stop-color="#f5a623" />
          <stop offset="100%" stop-color="#ff5464" />
        </linearGradient>
      </defs>
      <path
        :d="`M ${CENTER - RADIUS} ${CENTER} A ${RADIUS} ${RADIUS} 0 0 1 ${CENTER + RADIUS} ${CENTER}`"
        fill="none"
        stroke="rgba(38,49,64,0.9)"
        stroke-width="13"
        stroke-linecap="round"
      />
      <path
        v-if="clamped > 0"
        :d="arcPath"
        fill="none"
        stroke="url(#gaugeGrad)"
        stroke-width="13"
        stroke-linecap="round"
        class="gauge-arc"
      />
      <text :x="CENTER" :y="CENTER - 18" text-anchor="middle" class="gauge-value" :style="{ fill: tempColor }">
        {{ clamped.toFixed(0) }}
      </text>
      <text :x="CENTER" :y="CENTER + 2" text-anchor="middle" class="gauge-label">{{ tempLabel }}</text>
    </svg>
    <p v-if="caption" class="gauge-caption">{{ caption }}</p>
  </div>
</template>

<style scoped>
.gauge-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.gauge-svg {
  width: 190px;
  height: 116px;
}
.gauge-arc {
  filter: drop-shadow(0 0 6px rgba(34, 211, 238, 0.4));
}
.gauge-value {
  font-size: 34px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}
.gauge-label {
  font-size: 13px;
  fill: var(--text-dim);
}
.gauge-caption {
  color: var(--text-dim);
  font-size: 11.5px;
  margin: 2px 0 0;
  text-align: center;
}
</style>
