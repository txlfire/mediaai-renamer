<script setup lang="ts">
import { computed } from "vue";

import { textByteLength, truncateText } from "../utils/displayFormat";

const props = withDefaults(
  defineProps<{
    value: unknown;
    maxLength?: number;
  }>(),
  {
    maxLength: 10,
  },
);

const fullText = computed(() => String(props.value ?? "-"));
const displayText = computed(() => truncateText(fullText.value, props.maxLength));
const needsTooltip = computed(() => props.maxLength > 0 && textByteLength(fullText.value) > props.maxLength);
</script>

<template>
  <el-tooltip
    :disabled="!needsTooltip"
    effect="light"
    placement="top-start"
    popper-class="raw-text-tooltip"
  >
    <template #content>
      <span class="raw-tooltip-text">{{ fullText }}</span>
    </template>
    <span class="text-cell">{{ displayText }}</span>
  </el-tooltip>
</template>
