<script setup lang="ts" generic="T extends { id: number }">
import { ArrowLeft, ArrowRight, Refresh } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, shallowRef, ref, watch } from "vue";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { isSessionChangedError } from "../utils/sessionGeneration";

const props = defineProps<{
  modelValue?: number;
  contextKey?: string | number;
  loadPage: (page: number, query: string) => Promise<{ items: T[]; total: number; page: number }>;
  loadSelected: (id: number) => Promise<T>;
  optionLabel: (item: T) => string;
  remoteSearch?: boolean;
  disabled?: boolean;
  placeholder?: string;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: number | undefined];
  change: [value: number | undefined];
  selected: [item: T | null];
}>();
const rows = shallowRef<T[]>([]);
const selected = shallowRef<T | null>(null);
const loading = ref(false);
const page = ref(1);
const total = ref(0);
const query = ref("");
const error = ref("");
const selectedError = ref(false);
let version = 0;
let selectionVersion = 0;
let active = true;
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 50)));
const selectedOutsidePage = computed(() => selected.value && !rows.value.some(item => item.id === selected.value?.id));

async function loadPage(targetPage = 1) {
  if (!active || props.disabled) return;
  const request = ++version;
  loading.value = true;
  error.value = "";
  rows.value = [];
  try {
    const result = await props.loadPage(targetPage, query.value);
    if (request !== version) return;
    rows.value = result.items;
    total.value = result.total;
    page.value = result.page;
  } catch (requestError) {
    if (isSessionChangedError(requestError)) return;
    if (request !== version) return;
    rows.value = [];
    total.value = 0;
    error.value = messages.selectors.loadFailed;
  } finally {
    if (request === version) loading.value = false;
  }
}

async function resolveSelection() {
  const request = ++selectionVersion;
  selected.value = null;
  selectedError.value = false;
  emit("selected", null);
  const id = props.modelValue;
  if (!id || !active) return;
  try {
    const result = await props.loadSelected(id);
    if (request !== selectionVersion || id !== props.modelValue) return;
    selected.value = result;
    emit("selected", result);
  } catch (requestError) {
    if (isSessionChangedError(requestError)) return;
    if (request === selectionVersion) selectedError.value = true;
  }
}

function setValue(value: unknown) {
  const normalized = typeof value === "number" && Number.isInteger(value) && value > 0 ? value : undefined;
  emit("update:modelValue", normalized);
  emit("change", normalized);
}
function search(value: string) {
  query.value = value;
  void loadPage(1);
}
function openDropdown(open: boolean) {
  if (open) {
    query.value = "";
    void loadPage(1);
    void resolveSelection();
  }
}
watch(() => [props.modelValue, props.contextKey], () => { void resolveSelection(); }, { immediate: true });
watch(() => props.contextKey, () => {
  version += 1;
  rows.value = [];
  page.value = 1;
  total.value = 0;
  loading.value = false;
  error.value = "";
});
onBeforeUnmount(() => { active = false; version += 1; selectionVersion += 1; });
</script>

<template>
  <el-select :model-value="modelValue" :disabled="disabled" :placeholder="placeholder" clearable
    :loading="loading" :filterable="remoteSearch" :remote="remoteSearch" :remote-method="search"
    @update:model-value="setValue" @visible-change="openDropdown">
    <el-option v-if="selectedOutsidePage && selected" :value="selected.id" :label="optionLabel(selected)" />
    <el-option v-if="modelValue && !selected && !rows.some(item => item.id === modelValue)"
      :value="modelValue" :label="selectedError ? `${messages.selectors.selectionUnavailable} #${modelValue}` : `#${modelValue}`" disabled />
    <el-option v-for="item in rows" :key="item.id" :value="item.id" :label="optionLabel(item)" />
    <template #footer>
      <div class="paged-select-footer">
        <span v-if="error" role="alert">{{ error }}</span>
        <span v-else>{{ formatMessage(messages.common.totalRows, { total }) }} {{ page }}/{{ pageCount }}</span>
        <el-button v-if="error" text :icon="Refresh" :aria-label="messages.common.refresh" :title="messages.common.refresh" @click.stop="loadPage(1)" />
        <el-button text :icon="ArrowLeft" :disabled="loading || page <= 1" :aria-label="messages.common.previousPage"
          :title="messages.common.previousPage" @click.stop="loadPage(page - 1)" />
        <el-button text :icon="ArrowRight" :disabled="loading || page >= pageCount" :aria-label="messages.common.nextPage"
          :title="messages.common.nextPage" @click.stop="loadPage(page + 1)" />
      </div>
    </template>
  </el-select>
</template>

<style scoped>
.paged-select-footer { display: flex; align-items: center; gap: 4px; min-width: 0; font-size: 13px; }
.paged-select-footer span { flex: 1; overflow-wrap: anywhere; color: var(--el-text-color-secondary); }
.paged-select-footer .el-button { margin-left: 0; flex: 0 0 28px; width: 28px; height: 28px; }
</style>
