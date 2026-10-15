<script setup lang="ts">
import { computed, ref } from "vue";

import { textByteLength, truncateText } from "../utils/displayFormat";

const props = withDefaults(
  defineProps<{
    value: unknown;
    maxLength?: number;
    focusable?: boolean;
  }>(),
  {
    maxLength: 10,
    focusable: true,
  },
);

const fullText = computed(() => String(props.value ?? "-"));
const displayText = computed(() => truncateText(fullText.value, props.maxLength));
const needsTooltip = computed(() => props.maxLength > 0 && textByteLength(fullText.value) > props.maxLength);
const tooltipVisible = ref(false);

function dismissTooltip(event: KeyboardEvent) {
  if (!tooltipVisible.value) return;
  event.stopPropagation();
  tooltipVisible.value = false;
}
</script>

<template>
  <el-tooltip :trigger-keys="[]"
    v-model:visible="tooltipVisible"
    :trigger="['hover', 'focus']"
    :disabled="!needsTooltip"
    effect="light"
    placement="top-start"
    popper-class="raw-text-tooltip"
  >
    <template #content>
      <span class="raw-tooltip-text">{{ fullText }}</span>
    </template>
    <span class="text-cell" :tabindex="needsTooltip && focusable ? 0 : undefined" @keydown.esc="dismissTooltip">{{ displayText }}</span>
  </el-tooltip>
</template>
