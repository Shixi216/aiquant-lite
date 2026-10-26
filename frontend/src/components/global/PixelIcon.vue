<script setup lang="ts">
/**
 * 像素风图标（单色，随主题 currentColor 着色）。
 * 图形数据精选自 pixelarticons（MIT），见 domain/pixelIconPaths.ts；
 * 尺寸默认 16px，与按钮文字基线对齐，shape-rendering 保持像素锐利但不粗糙。
 */
import { computed } from "vue";

import { PIXEL_PATHS } from "../../domain/pixelIconPaths";

const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 16 });

const path = computed(() => PIXEL_PATHS[props.name] ?? "");
</script>

<template>
  <svg
    class="pixel-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="currentColor"
    shape-rendering="crispEdges"
    aria-hidden="true"
  >
    <path :d="path" />
  </svg>
</template>

<style scoped>
.pixel-icon {
  display: inline-block;
  vertical-align: -0.2em;
  flex-shrink: 0;
}
</style>
