<script setup lang="ts">
/**
 * 财富实验室统一日期输入：用户可见文本恒为 YYYY-MM-DD，
 * 底层 v-model 值保持契约所需的 YYYY-MM-DD 字符串，不做任何格式转换。
 * 手动输入非法格式时标红并回退上次合法值。
 */
import { computed, ref, watch } from "vue";

const props = defineProps<{
  modelValue: string;
  label?: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const text = ref(props.modelValue);
const invalid = ref(false);

watch(
  () => props.modelValue,
  (value) => {
    text.value = value;
    invalid.value = false;
  },
);

function isValidDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function commit(): void {
  const value = text.value.trim();
  if (isValidDate(value)) {
    invalid.value = false;
    if (value !== props.modelValue) emit("update:modelValue", value);
  } else {
    invalid.value = true;
    text.value = props.modelValue; // 回退上次合法值
  }
}
</script>

<template>
  <label class="wl-date">
    <span v-if="label" class="wl-date-label">{{ label }}</span>
    <input
      v-model="text"
      type="text"
      class="wl-date-input"
      :class="{ invalid }"
      placeholder="YYYY-MM-DD"
      maxlength="10"
      :disabled="disabled"
      :title="invalid ? '日期格式无效，应为 YYYY-MM-DD' : '格式：YYYY-MM-DD'"
      spellcheck="false"
      @change="commit"
      @keyup.enter="($event.target as HTMLInputElement).blur()"
    />
  </label>
</template>

<style scoped>
.wl-date { display: flex; flex-direction: column; gap: 4px; }
.wl-date-label { font-size: 11.5px; color: var(--text-dim); }
.wl-date-input { min-width: 128px; font-variant-numeric: tabular-nums; letter-spacing: 0.5px; }
.wl-date-input.invalid { border-color: var(--warn); box-shadow: 0 0 0 3px rgba(245, 166, 35, 0.15); }
</style>
