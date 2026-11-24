<script setup lang="ts">
import { useInterfaceLanguage } from "../locales/interfaceLanguage";

defineProps<{ showSingle?: boolean }>();
const { language, messages, options, setLanguage } = useInterfaceLanguage();
</script>

<template>
  <el-select
    v-if="showSingle || options.length > 1"
    :model-value="language"
    :aria-label="messages.app.languageLabel"
    :disabled="options.length < 2"
    size="small"
    @update:model-value="setLanguage"
  >
    <el-option v-for="option in options" :key="option.value" :label="option.label" :value="option.value" />
  </el-select>
</template>
