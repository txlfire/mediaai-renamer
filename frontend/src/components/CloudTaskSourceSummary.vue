<script setup lang="ts">
import { computed } from "vue";
import { resolveCloudTaskBinding, type CloudTaskBinding } from "../utils/cloudTaskBinding";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
const props = defineProps<{ value: CloudTaskBinding; compact?: boolean }>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudTaskSource);
const binding = computed(() => { try { return resolveCloudTaskBinding(props.value); } catch { return null; } });
</script>
<template>
  <dl v-if="binding?.mode === 'bound'" class="cloud-task-source" :class="{ 'source-compact': compact }">
    <dt>{{ text.original }}</dt><dd>{{ binding.source.connectionName }} / {{ binding.source.directoryName }}</dd>
    <template v-if="binding.source.connectionName !== binding.source.currentConnectionName || binding.source.directoryName !== binding.source.currentDirectoryName"><dt>{{ text.currentName }}</dt><dd>{{ binding.source.currentConnectionName }} / {{ binding.source.currentDirectoryName }}</dd></template>
    <template v-if="!compact"><dt>{{ text.path }}</dt><dd>{{ binding.source.selectedPath }}</dd></template>
  </dl>
  <p v-else-if="!binding || binding.mode === 'legacy_unbound'" class="cloud-task-source-note" role="status">{{ binding ? text.unbound : text.invalid }}</p>
  <span v-else-if="compact">{{ messages.cloudHistory.single }}</span>
</template>
<style scoped>
.cloud-task-source { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 6px 12px; margin: 0 0 12px; padding: 10px 12px; border: 1px solid var(--el-border-color-light); border-radius: 6px; background: var(--el-fill-color-light); font-size: 13px; line-height: 1.6; min-width: 0; }
.cloud-task-source dt { color: var(--el-text-color-secondary); }
.cloud-task-source dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.cloud-task-source-note { font-size: 13px; line-height: 1.6; color: var(--el-color-warning); margin: 0 0 12px; }
.cloud-task-source.source-compact { border: 0; background: transparent; padding: 0; margin: 0; grid-template-columns: minmax(0, 1fr); gap: 2px; }
.source-compact dt { font-size: 12px; }
@media (max-width: 480px) { .cloud-task-source { grid-template-columns: minmax(0, 1fr); gap: 4px; } }
</style>
