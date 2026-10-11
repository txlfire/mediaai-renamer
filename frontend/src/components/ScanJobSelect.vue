<script setup lang="ts">
import { fetchScanJob, fetchScanJobsPage, type ScanJob } from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";
import PagedRecordSelect from "./PagedRecordSelect.vue";
const props = defineProps<{ modelValue?: number; mediaSourceId?: number; placeholder?: string; disabled?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: number | undefined]; change: [value: number | undefined] }>();
async function loadPage(page: number) {
  if (!props.mediaSourceId) return { items: [] as ScanJob[], total: 0, page: 1 };
  return fetchScanJobsPage({ media_source_id: props.mediaSourceId, page, page_size: 50 });
}
async function loadSelected(id: number) {
  if (!props.mediaSourceId) throw new Error(messages.selectors.selectionUnavailable);
  const item = await fetchScanJob(id);
  if (item.media_source_id !== props.mediaSourceId) throw new Error(messages.selectors.selectionUnavailable);
  return item;
}
</script>
<template>
  <PagedRecordSelect :model-value="modelValue" :load-page="loadPage" :load-selected="loadSelected"
    :option-label="(item: ScanJob) => `${messages.common.taskLabel} ${item.id}`" :context-key="mediaSourceId"
    :disabled="disabled || !mediaSourceId" :placeholder="placeholder" @update:model-value="emit('update:modelValue', $event)"
    @change="emit('change', $event)" />
</template>
