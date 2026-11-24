<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { fetchCloudConnectionChoices, fetchCloudDirectoryChoices, CloudConnectionRequestError, type CloudConnectionChoice, type CloudDirectoryChoice, type CloudPage } from "../api/cloudConnections";
import { inventoryRequestId } from "../api/cloudInventory";
import { useAuthStore } from "../stores/auth";
import { getSessionGeneration } from "../utils/sessionGeneration";
import type { CloudHistoryFilter } from "../utils/cloudHistorySource";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";

const props = defineProps<{ disabled?: boolean }>();
const emit = defineEmits<{ change: [value: CloudHistoryFilter] }>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudHistory), auth = useAuthStore();
const session = getSessionGeneration(), userId = auth.currentUser?.id;
const connectionId = ref(""), directoryId = ref("");
const connections = ref<CloudConnectionChoice[]>([]), directories = ref<CloudDirectoryChoice[]>([]);
const connectionPage = ref(1), directoryPage = ref(1), connectionTotal = ref(0), directoryTotal = ref(0);
const connectionBusy = ref(false), directoryBusy = ref(false), error = ref("");
const disposed = ref(false);
let active = true, connectionSequence = 0, directorySequence = 0, connectionSearch = "", directorySearch = "";
function valid() { return active && !disposed.value && session === getSessionGeneration() && userId === auth.currentUser?.id
  && Boolean(userId) && (typeof document === "undefined" || !document.hidden); }
function current(version: number, directory = false) { return valid() && version === (directory ? directorySequence : connectionSequence); }
function clear() {
  connectionSequence++; directorySequence++; connections.value = []; directories.value = [];
  connectionTotal.value = 0; directoryTotal.value = 0; connectionBusy.value = false; directoryBusy.value = false;
}
function expire() { disposed.value = true; clear(); connectionId.value = ""; directoryId.value = ""; error.value = text.value.expired; }
function publish() { emit("change", connectionId.value ? { connectionId: connectionId.value, ...(directoryId.value ? { directoryProfileId: directoryId.value } : {}) } : {}); }
function validate<T extends CloudConnectionChoice | CloudDirectoryChoice>(result: CloudPage<T>, page: number, parent?: string) {
  if (!result || result.networkAccess !== false || result.page !== page || result.pageSize !== 10
    || !Number.isSafeInteger(result.total) || result.total < 0 || !Array.isArray(result.items) || result.items.length > 10
    || new Set(result.items.map(item => item.id)).size !== result.items.length || result.items.some(item => !inventoryRequestId(item.id)
      || typeof item.name !== "string" || item.name.length > 80 || !["enabled", "disabled"].includes(item.state)
      || parent && (!("connectionId" in item) || item.connectionId !== parent))) throw new Error("invalid_history_choices");
  return result;
}
function failed(cause: unknown) { error.value = cause instanceof CloudConnectionRequestError && cause.reasonCode === "storage_not_migrated" ? text.value.legacy : text.value.failed; }
async function loadConnections(page = 1, search = connectionSearch) {
  if (!valid() || props.disabled) return;
  const version = ++connectionSequence; connectionBusy.value = true; error.value = ""; connectionSearch = search;
  connections.value = [];
  try {
    const value = validate(await fetchCloudConnectionChoices({ page, pageSize: 10, search }), page);
    if (current(version)) { connections.value = value.items; connectionPage.value = page; connectionTotal.value = value.total; }
  } catch (cause) { if (current(version)) failed(cause); }
  finally { if (current(version)) connectionBusy.value = false; }
}
async function loadDirectories(page = 1, search = directorySearch) {
  if (!valid() || props.disabled || !connectionId.value) return;
  const id = connectionId.value, version = ++directorySequence; directoryBusy.value = true; error.value = ""; directorySearch = search;
  directories.value = [];
  try {
    const value = validate(await fetchCloudDirectoryChoices(id, { page, pageSize: 10, search }), page, id);
    if (current(version, true) && id === connectionId.value) { directories.value = value.items; directoryPage.value = page; directoryTotal.value = value.total; }
  } catch (cause) { if (current(version, true)) failed(cause); }
  finally { if (current(version, true)) directoryBusy.value = false; }
}
function chooseConnection(value: unknown) {
  if (!valid() || props.disabled || value && !connections.value.some(item => item.id === value)) return;
  directorySequence++; directoryBusy.value = false; directories.value = []; directoryTotal.value = 0; directorySearch = "";
  connectionId.value = typeof value === "string" ? value : ""; directoryId.value = ""; publish();
  if (connectionId.value) void loadDirectories();
}
function chooseDirectory(value: unknown) {
  if (!valid() || props.disabled || !connectionId.value || value && !directories.value.some(item => item.id === value)) return;
  directoryId.value = typeof value === "string" ? value : ""; publish();
}
function hidden() { if (document.hidden) expire(); }
watch(() => [auth.currentUser?.id, JSON.stringify(auth.currentUser?.permissions)], () => { expire(); }, { flush: "sync" });
watch(() => props.disabled, value => { if (!value && !connections.value.length && !connectionId.value) void loadConnections(); });
onMounted(() => {
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", hidden);
  if (typeof window !== "undefined") window.addEventListener("pagehide", expire);
  void loadConnections();
});
onBeforeUnmount(() => { active = false; clear(); if (typeof document !== "undefined") document.removeEventListener("visibilitychange", hidden);
  if (typeof window !== "undefined") window.removeEventListener("pagehide", expire); });
</script>

<template>
  <div class="cloud-history-filter">
    <div class="history-filter-fields">
      <el-select :model-value="connectionId" :aria-label="text.connection" :placeholder="text.allConnections" clearable filterable remote
        :remote-method="(value: string) => loadConnections(1, value)" :loading="connectionBusy" :disabled="disabled || disposed" @update:model-value="chooseConnection">
        <el-option v-for="item in connections" :key="item.id" :value="item.id" :label="`${item.name} · ${messages.cloudConnections.states[item.state]}`" />
        <template #footer><el-pagination :current-page="connectionPage" :total="connectionTotal" :page-size="10" :pager-count="5" layout="prev, pager, next" :disabled="disabled || connectionBusy || disposed" @current-change="loadConnections" /></template>
      </el-select>
      <el-select :model-value="directoryId" :aria-label="text.directory" :placeholder="text.allDirectories" clearable filterable remote
        :remote-method="(value: string) => loadDirectories(1, value)" :loading="directoryBusy" :disabled="disabled || disposed || !connectionId" @update:model-value="chooseDirectory">
        <el-option v-for="item in directories" :key="item.id" :value="item.id" :label="`${item.name} · ${messages.cloudConnections.states[item.state]}`" />
        <template #footer><el-pagination :current-page="directoryPage" :total="directoryTotal" :page-size="10" :pager-count="5" layout="prev, pager, next" :disabled="disabled || directoryBusy || disposed" @current-change="loadDirectories" /></template>
      </el-select>
      <el-tooltip :content="text.refresh" :trigger-keys="[]"><el-button :icon="Refresh" :aria-label="text.refresh" :disabled="disabled || disposed || connectionBusy || directoryBusy" @click="connectionId ? loadDirectories() : loadConnections()" /></el-tooltip>
    </div>
    <p :class="{ 'history-filter-error': error }" :role="error ? 'status' : undefined">{{ error || text.hint }}</p>
  </div>
</template>
<style scoped>
.cloud-history-filter { min-width: 0; margin-bottom: 12px; }
.history-filter-fields { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; gap: 8px; }
.cloud-history-filter p { color: var(--text-muted); font-size: 13px; line-height: 1.6; margin: 8px 0; overflow-wrap: anywhere; }
.cloud-history-filter .history-filter-error { color: var(--el-color-warning); }
@media (max-width: 640px) { .history-filter-fields { grid-template-columns: minmax(0, 1fr) auto; } .history-filter-fields > .el-select:first-child { grid-column: 1 / -1; } }
</style>
