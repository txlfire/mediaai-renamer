<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { CloudConnectionRequestError, fetchCloudConnectionChoices, fetchCloudDirectoryChoices, type CloudConnectionChoice, type CloudDirectoryChoice } from "../api/cloudConnections";
import { createCloudSourceDraft, type CloudSourceTicket } from "../utils/cloudSourceDraft";
import { useAuthStore } from "../stores/auth";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";

const props = defineProps<{ disabled?: boolean; allowDisabled?: boolean }>();
const emit = defineEmits<{ change: [ticket: CloudSourceTicket | null] }>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudSourcePicker), auth = useAuthStore();
const ownerSession = getSessionGeneration(), ownerId = auth.currentUser?.id;
const model = createCloudSourceDraft(() => emit("change", null));
const connections = ref<CloudConnectionChoice[]>([]), directories = ref<CloudDirectoryChoice[]>([]);
const connectionPage = ref(1), directoryPage = ref(1), connectionTotal = ref(0), directoryTotal = ref(0);
const connectionLoading = ref(false), directoryLoading = ref(false), error = ref("");
const connectionId = computed(() => model.draft.value.connection?.id ?? "");
const directoryId = computed(() => model.draft.value.directory?.id ?? "");
const selectedPath = computed(() => model.draft.value.selectedPath);
const blocked = computed(() => props.disabled || !auth.hasPermission("scan:run"));
let active = true, disposed = false, connectionSequence = 0, directorySequence = 0;
let connectionSearch = "", directorySearch = "";
function validOwner() { return active && !disposed && ownerSession === getSessionGeneration()
  && ownerId === auth.currentUser?.id && auth.hasPermission("scan:run")
  && (typeof document === "undefined" || !document.hidden); }
function clearLists() { connections.value = []; directories.value = []; connectionTotal.value = 0; directoryTotal.value = 0; connectionLoading.value = false; directoryLoading.value = false; connectionSequence++; directorySequence++; }
function expire() { model.dispose(); disposed = true; clearLists(); error.value = messages.value.cloudInventory.startFlow.sessionChanged; }
function showError(cause: unknown) {
  error.value = isSessionChangedError(cause) ? messages.value.cloudInventory.startFlow.sessionChanged
    : cause instanceof CloudConnectionRequestError && cause.reasonCode === "storage_not_migrated" ? messages.value.cloudDriveSettings.storageNotMigrated : text.value.failed;
}
function selectable(state: CloudConnectionChoice["state"]) { return state === "enabled" || props.allowDisabled === true && state === "disabled"; }
async function loadConnections(page = 1, search = connectionSearch) {
  if (blocked.value || !validOwner()) return;
  model.selectConnection(null); directories.value = []; directoryTotal.value = 0; directorySequence++; directoryLoading.value = false;
  const version = ++connectionSequence; connectionSearch = search; connections.value = []; connectionLoading.value = true; error.value = "";
  try {
    const result = await fetchCloudConnectionChoices({ page, pageSize: 10, search });
    if (!validOwner() || version !== connectionSequence) return;
    connections.value = result.items; connectionTotal.value = result.total; connectionPage.value = result.page;
  } catch (cause) { if (validOwner() && version === connectionSequence) showError(cause); }
  finally { if (validOwner() && version === connectionSequence) connectionLoading.value = false; }
}
async function loadDirectories(page = 1, search = directorySearch) {
  if (blocked.value || !validOwner() || !connectionId.value) return;
  model.selectDirectory(null);
  const id = connectionId.value, version = ++directorySequence; directorySearch = search;
  directories.value = []; directoryLoading.value = true; error.value = "";
  try {
    const result = await fetchCloudDirectoryChoices(id, { page, pageSize: 10, search });
    if (!validOwner() || version !== directorySequence || id !== connectionId.value) return;
    directories.value = result.items; directoryTotal.value = result.total; directoryPage.value = result.page;
  } catch (cause) { if (validOwner() && version === directorySequence && id === connectionId.value) showError(cause); }
  finally { if (validOwner() && version === directorySequence) directoryLoading.value = false; }
}
async function chooseConnection(value: unknown) {
  if (blocked.value || !validOwner()) return;
  directorySequence++; directories.value = []; directoryTotal.value = 0; directorySearch = "";
  const choice = connections.value.find(item => item.id === value && selectable(item.state)) ?? null;
  model.selectConnection(choice);
  if (choice) await loadDirectories();
}
function publish() {
  try { emit("change", model.capture()); error.value = ""; }
  catch { error.value = text.value.invalidPath; }
}
function chooseDirectory(value: unknown) {
  if (blocked.value || !validOwner()) return;
  const choice = directories.value.find(item => item.id === value && selectable(item.state)) ?? null;
  try { model.selectDirectory(choice); if (choice) publish(); }
  catch { error.value = text.value.failed; }
}
function changePath(value: string) { if (!blocked.value && validOwner()) { model.selectPath(value); publish(); } }
function searchConnections(value: string) { void loadConnections(1, value); }
function searchDirectories(value: string) { void loadDirectories(1, value); }
function connectionPageChanged(value: number) { void loadConnections(value); }
function directoryPageChanged(value: number) { void loadDirectories(value); }
function visibility() {
  if (document.hidden) { model.suspend(); clearLists(); }
  else if (validOwner() && model.resume()) { model.selectConnection(null); error.value = text.value.reload; }
}
watch(() => [auth.currentUser?.id, auth.hasPermission("scan:run")], () => { if (!validOwner()) expire(); }, { flush: "sync" });
onMounted(() => { if (typeof document !== "undefined") document.addEventListener("visibilitychange", visibility); void loadConnections(); });
onBeforeUnmount(() => { active = false; model.dispose(); clearLists(); if (typeof document !== "undefined") document.removeEventListener("visibilitychange", visibility); });
defineExpose({ isCurrent: model.isCurrent });
</script>

<template>
  <div class="cloud-source-picker">
    <p v-if="error" class="source-error" role="alert">{{ error }}</p>
    <div class="source-picker-fields">
      <el-form-item :label="text.connection">
        <div class="source-picker-control"><el-select :model-value="connectionId" :aria-label="text.connection" :placeholder="text.chooseConnection" filterable remote :remote-method="searchConnections" :loading="connectionLoading" :disabled="blocked" clearable @update:model-value="chooseConnection">
          <el-option v-for="choice in connections" :key="choice.id" :value="choice.id" :label="`${choice.name} · ${messages.cloudConnections.states[choice.state]}`" :disabled="!selectable(choice.state)" />
          <template #footer><el-pagination :current-page="connectionPage" :total="connectionTotal" :page-size="10" :pager-count="5" layout="prev, pager, next" :disabled="blocked || connectionLoading" @current-change="connectionPageChanged" /></template>
        </el-select><el-tooltip :content="text.reload" :trigger-keys="[]"><el-button text :icon="Refresh" :aria-label="text.reload" :disabled="blocked || connectionLoading" @click="loadConnections()" /></el-tooltip></div>
      </el-form-item>
      <el-form-item :label="text.directory">
        <el-select :model-value="directoryId" :aria-label="text.directory" :placeholder="text.chooseDirectory" filterable remote :remote-method="searchDirectories" :loading="directoryLoading" :disabled="blocked || !connectionId || connectionLoading" clearable @update:model-value="chooseDirectory">
          <el-option v-for="choice in directories" :key="choice.id" :value="choice.id" :label="`${choice.name} · ${messages.cloudConnections.states[choice.state]}`" :disabled="!selectable(choice.state)" />
          <template #footer><el-pagination :current-page="directoryPage" :total="directoryTotal" :page-size="10" :pager-count="5" layout="prev, pager, next" :disabled="blocked || directoryLoading" @current-change="directoryPageChanged" /></template>
        </el-select>
      </el-form-item>
    </div>
    <el-form-item v-if="directoryId" :label="text.path"><el-input :model-value="selectedPath" :aria-label="text.path" :disabled="blocked" maxlength="4096" autocomplete="off" @update:model-value="changePath" /></el-form-item>
    <p class="source-help">{{ text.scope }}</p>
  </div>
</template>
<style scoped>
.cloud-source-picker { min-width: 0; }
.source-picker-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.source-picker-control { display: flex; align-items: center; gap: 4px; width: 100%; min-width: 0; }
.source-picker-control .el-select { min-width: 0; flex: 1; }
.source-picker-control .el-button { flex: 0 0 32px; width: 32px; padding: 0; }
.source-help { font-size: 13px; line-height: 1.6; color: var(--el-text-color-secondary); }
.source-error { color: var(--el-color-danger); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.cloud-source-picker :deep(.el-form-item__content) { min-width: 0; }
@media (max-width: 640px) { .source-picker-fields { grid-template-columns: minmax(0, 1fr); gap: 0; } }
</style>
