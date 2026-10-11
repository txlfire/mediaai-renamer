<script setup lang="ts">
import { fetchMediaSource, fetchMediaSourcesPage, type MediaSource } from "../api/client";
import PagedRecordSelect from "./PagedRecordSelect.vue";
const props = defineProps<{ modelValue?: number; enabledOnly?: boolean; placeholder?: string }>();
const emit = defineEmits<{ "update:modelValue": [value: number | undefined]; change: [value: number | undefined]; selected: [item: MediaSource | null] }>();
function loadPage(page: number, query: string) {
  return fetchMediaSourcesPage({ page, page_size: 50, q: query, enabled_only: props.enabledOnly });
}
</script>
<template>
  <PagedRecordSelect :model-value="modelValue" :load-page="loadPage" :load-selected="fetchMediaSource"
    :option-label="(item: MediaSource) => item.name" :context-key="enabledOnly ? 'enabled' : 'all'"
    :placeholder="placeholder" remote-search @update:model-value="emit('update:modelValue', $event)"
    @change="emit('change', $event)" @selected="emit('selected', $event)" />
</template>
