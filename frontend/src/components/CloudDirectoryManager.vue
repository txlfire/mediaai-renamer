<script setup lang="ts">
import FullscreenTablePanel from "./FullscreenTablePanel.vue";
import { computed, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Edit, Setting, Plus, Refresh, CircleCheck, CircleClose, Box } from "@element-plus/icons-vue";
import { createCloudDirectoryProfile, fetchCloudDirectoryProfiles, renameCloudDirectoryProfile,
  setCloudDirectoryState, updateCloudDirectoryPolicy, type CloudDirectoryProfile, type CloudSourceState } from "../api/cloudConnections";
import { formatMessage } from "../locales/zh-CN";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { useCloudSettingsScope } from "../utils/useCloudSettingsScope";

const props = defineProps<{ connectionId: string }>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudConnections), settingsText = computed(() => messages.value.cloudDriveSettings);
const rows = ref<CloudDirectoryProfile[]>([]), page = ref(1), total = ref(0), search = ref("");
const editor = ref<"create" | "name" | "policy" | null>(null), original = ref<CloudDirectoryProfile | null>(null), reloadRequired = ref(false);
const form = reactive({ name: "", approvedRoot: "", mappingKey: "", quarantineFolderName: "_MediaAI_Quarantine",
  webExecutionEnabled: false, directoryRenameEnabled: false, quarantineEnabled: false });
let initialDraft = "", requestId = "";
const dirty = computed(() => editor.value !== null && JSON.stringify(form) !== initialDraft);
function clearEditor() { editor.value = null; original.value = null; Object.assign(form, { name: "", approvedRoot: "", mappingKey: "", quarantineFolderName: "_MediaAI_Quarantine", webExecutionEnabled: false, directoryRenameEnabled: false, quarantineEnabled: false }); requestId = ""; }
function reset() { clearEditor(); rows.value = []; total.value = 0; reloadRequired.value = false; }
const scope = useCloudSettingsScope(reset, load), { busy, error } = scope;
async function discard() {
  if (!dirty.value) return true;
  try { await ElMessageBox.confirm(text.value.discard, text.value.discardTitle, { type: "warning", confirmButtonText: text.value.close, cancelButtonText: text.value.cancel }); return true; }
  catch { return false; }
}
async function load(nextPage = page.value) {
  const ticket = scope.begin(); if (!ticket) return;
  try {
    if (!await discard() || !scope.current(ticket)) return;
    clearEditor(); rows.value = [];
    const result = await fetchCloudDirectoryProfiles(props.connectionId, { page: nextPage, pageSize: 10, search: search.value });
    if (scope.current(ticket)) { rows.value = result.items; page.value = result.page; total.value = result.total; reloadRequired.value = false; }
  } catch (cause) { scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function open(mode: NonNullable<typeof editor.value>, row?: CloudDirectoryProfile) {
  if (reloadRequired.value) return;
  const ticket = scope.begin(); if (!ticket) return;
  try {
  if (!await discard() || !scope.current(ticket)) return;
  clearEditor(); original.value = row ?? null;
  if (row) Object.assign(form, { name: row.name, approvedRoot: row.approvedRoot, mappingKey: row.mappingKey,
    quarantineFolderName: row.quarantineFolderName, webExecutionEnabled: row.webExecutionEnabled,
    directoryRenameEnabled: row.directoryRenameEnabled, quarantineEnabled: row.quarantineEnabled });
  requestId = mode === "create" ? crypto.randomUUID() : ""; initialDraft = JSON.stringify(form); editor.value = mode;
  } finally { scope.finish(ticket); }
}
function apply(row: CloudDirectoryProfile) {
  const index = rows.value.findIndex(item => item.id === row.id);
  if (index >= 0) rows.value[index] = row;
  else { rows.value = [row, ...rows.value].slice(0, 10); total.value++; }
}
async function save() {
  if (busy.value || reloadRequired.value || !editor.value) return;
  const mode = editor.value, target = original.value;
  if ((mode !== "policy" && !form.name.trim()) || (mode === "create" && (!form.approvedRoot.startsWith("/") || !form.mappingKey.trim()))) { error.value = text.value.invalid; return; }
  const ticket = scope.begin(); if (!ticket) return;
  let sent = false;
  try {
    await ElMessageBox.confirm(mode === "policy" ? `${text.value.confirm} ${settingsText.value.writeRisk}` : text.value.confirm, text.value.confirmTitle,
      { type: "warning", confirmButtonText: text.value.save, cancelButtonText: text.value.cancel });
    if (!scope.current(ticket)) return;
    sent = true;
    const result = mode === "create" ? await createCloudDirectoryProfile(props.connectionId, { ...form, requestId })
      : mode === "name" && target ? await renameCloudDirectoryProfile(props.connectionId, target.id, form.name, target.metadataRevision)
      : target ? await updateCloudDirectoryPolicy(props.connectionId, target.id, { ...form, expectedSecurityRevision: target.securityRevision }) : null;
    if (scope.current(ticket) && result) { apply(result); clearEditor(); ElMessage.success(text.value.saved); }
  } catch (cause) { if (scope.current(ticket) && sent) reloadRequired.value = true; scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function changeState(row: CloudDirectoryProfile, state: CloudSourceState) {
  if (busy.value || reloadRequired.value || editor.value) return;
  const ticket = scope.begin(); if (!ticket) return;
  let sent = false;
  try {
    await ElMessageBox.confirm(formatMessage(text.value.stateConfirm, { state: text.value.states[state] }), text.value.confirmTitle,
      { type: "warning", confirmButtonText: text.value.save, cancelButtonText: text.value.cancel });
    if (!scope.current(ticket)) return;
    sent = true;
    const result = await setCloudDirectoryState(props.connectionId, row.id, state, row.stateRevision);
    if (scope.current(ticket)) { apply(result); ElMessage.success(text.value.saved); }
  } catch (cause) { if (scope.current(ticket) && sent) reloadRequired.value = true; scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function closeEditor(done?: () => void) {
  const ticket = scope.begin(); if (!ticket) return;
  try { if (await discard() && scope.current(ticket)) { clearEditor(); done?.(); } }
  finally { scope.finish(ticket); }
}
defineExpose({ dirty, busy });
</script>

<template>
  <section class="cloud-directories">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="reloadRequired" :title="text.reloadRequired" type="warning" :closable="false" show-icon />
    <div class="directory-toolbar">
      <el-input v-model="search" :placeholder="text.search" :aria-label="text.search" maxlength="80" clearable :disabled="busy" @keyup.enter="load(1)" @clear="load(1)" />
      <el-button :icon="Refresh" :disabled="busy" @click="load()">{{ text.refresh }}</el-button>
      <el-button type="primary" :icon="Plus" :disabled="busy || reloadRequired" @click="open('create')">{{ text.addDirectory }}</el-button>
    </div>
    <div class="directory-table"><FullscreenTablePanel variant="embedded"><el-table :data="rows" row-key="id" border :empty-text="text.directoryEmpty">
      <el-table-column prop="name" :label="text.directoryName" min-width="140" show-overflow-tooltip />
      <el-table-column prop="approvedRoot" :label="settingsText.root" min-width="240" show-overflow-tooltip />
      <el-table-column prop="mappingKey" :label="settingsText.mapping" min-width="140" show-overflow-tooltip />
      <el-table-column :label="text.state" width="90"><template #default="{ row }"><el-tag :type="row.state === 'enabled' ? 'success' : 'info'">{{ text.states[row.state as CloudSourceState] }}</el-tag></template></el-table-column>
      <el-table-column :label="text.operations" width="160" fixed="right" align="center"><template #default="{ row }"><div class="table-actions">
        <el-tooltip :content="text.editName" :trigger-keys="[]"><el-button text class="table-action-button action-edit" :icon="Edit" :aria-label="text.editName" :disabled="busy || reloadRequired" @click="open('name', row)" /></el-tooltip>
        <el-tooltip :content="text.directoryPolicy" :trigger-keys="[]"><el-button text class="table-action-button action-edit" :icon="Setting" :aria-label="text.directoryPolicy" :disabled="busy || reloadRequired" @click="open('policy', row)" /></el-tooltip>
        <el-tooltip :content="row.state === 'enabled' ? text.disable : text.enable" :trigger-keys="[]"><el-button text class="table-action-button action-toggle" :icon="row.state === 'enabled' ? CircleClose : CircleCheck" :aria-label="row.state === 'enabled' ? text.disable : text.enable" :disabled="busy || reloadRequired" @click="changeState(row, row.state === 'enabled' ? 'disabled' : 'enabled')" /></el-tooltip>
        <el-tooltip :content="text.archive" :trigger-keys="[]"><el-button text class="table-action-button action-delete" :icon="Box" :aria-label="text.archive" :disabled="busy || reloadRequired || row.state === 'archived'" @click="changeState(row, 'archived')" /></el-tooltip>
      </div></template></el-table-column>
    </el-table>
      <el-pagination :current-page="page" :page-size="10" :total="total" :pager-count="5" layout="total, prev, pager, next" :disabled="busy" @current-change="load" />
    </FullscreenTablePanel></div>
    <el-dialog :model-value="editor !== null" :title="editor === 'create' ? text.addDirectory : editor === 'name' ? text.editName : text.directoryPolicy" width="min(640px, calc(100vw - 32px))" append-to-body :before-close="closeEditor" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-alert v-if="reloadRequired" :title="text.reloadRequired" type="warning" :closable="false" show-icon />
      <el-form label-position="top" :disabled="busy || reloadRequired" @submit.prevent="save">
        <el-form-item v-if="editor !== 'policy'" :label="text.directoryName" required><el-input v-model="form.name" maxlength="80" /></el-form-item>
        <template v-if="editor === 'create'">
          <el-form-item :label="settingsText.root" required><el-input v-model="form.approvedRoot" maxlength="4096" /></el-form-item>
          <el-form-item :label="settingsText.mapping" required><el-input v-model="form.mappingKey" maxlength="64" /></el-form-item>
        </template>
        <template v-if="editor !== 'name'">
          <el-form-item :label="settingsText.quarantineFolder"><el-input v-model="form.quarantineFolderName" maxlength="240" /></el-form-item>
          <p class="directory-help">{{ text.immutableRoot }}</p>
        </template>
        <template v-if="editor === 'policy'">
          <div class="directory-flags"><el-checkbox v-model="form.webExecutionEnabled">{{ settingsText.web }}</el-checkbox><el-checkbox v-model="form.directoryRenameEnabled">{{ settingsText.rename }}</el-checkbox><el-checkbox v-model="form.quarantineEnabled">{{ settingsText.quarantine }}</el-checkbox></div>
          <p class="directory-help">{{ settingsText.limits }}</p><p class="directory-help">{{ settingsText.writeRisk }}</p>
        </template>
        <p v-if="editor === 'create'" class="directory-help">{{ text.defaultOff }}</p>
      </el-form>
      <template #footer><el-button :disabled="busy" @click="closeEditor()">{{ text.cancel }}</el-button><el-button type="primary" :loading="busy" :disabled="reloadRequired || !dirty" @click="save">{{ text.save }}</el-button></template>
    </el-dialog>
  </section>
</template>

<style scoped>
.cloud-directories { display: grid; gap: 12px; min-width: 0; }
.directory-toolbar { display: flex; flex-wrap: wrap; gap: 10px; min-width: 0; }
.directory-toolbar .el-input { width: 220px; max-width: 100%; margin-right: auto; }
.directory-toolbar :deep(.el-button + .el-button) { margin-left: 0; }
.directory-table { width: 100%; min-width: 0; overflow: hidden; }
.directory-help { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.directory-flags { display: grid; gap: 8px; min-width: 0; }
.directory-flags :deep(.el-checkbox) { height: auto; margin: 0; min-height: 32px; }
.directory-flags :deep(.el-checkbox__label) { white-space: normal; }
.cloud-directories :deep(.el-pagination) { max-width: 100%; flex-wrap: wrap; gap: 4px; }
</style>
