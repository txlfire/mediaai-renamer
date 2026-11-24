<script setup lang="ts">
import FullscreenTablePanel from "./FullscreenTablePanel.vue";
import { computed, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Edit, Lock, Plus, Refresh, FolderOpened, CircleCheck, CircleClose, Box } from "@element-plus/icons-vue";
import { createCloudConnection, fetchCloudConnection, fetchCloudConnections, renameCloudConnection,
  setCloudConnectionState, updateCloudConnectionSecurity, type CloudConnectionRecord, type CloudSourceState } from "../api/cloudConnections";
import { formatMessage } from "../locales/zh-CN";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { useCloudSettingsScope } from "../utils/useCloudSettingsScope";
import CloudDirectoryManager from "./CloudDirectoryManager.vue";
import CloudConnectionGrantEditor from "./CloudConnectionGrantEditor.vue";
import CloudGlobalPolicyPanel from "./CloudGlobalPolicyPanel.vue";
import CloudProtectionDialog from "./CloudProtectionDialog.vue";

const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudConnections), settingsText = computed(() => messages.value.cloudDriveSettings);
const rows = ref<CloudConnectionRecord[]>([]), total = ref(0), page = ref(1), search = ref("");
const selected = ref<CloudConnectionRecord | null>(null), tab = ref("directories");
const protectionVisible = ref(false);
const directoryPanel = ref<{ dirty: boolean; busy: boolean } | null>(null);
const grantPanel = ref<{ dirty: boolean; busy: boolean } | null>(null);
const editor = ref<"create" | "name" | "security" | null>(null), original = ref<CloudConnectionRecord | null>(null);
const form = reactive({ name: "", endpoint: "", caCertificatePem: "" });
const reloadRequired = ref(false);
let initialDraft = "", requestId = "";
const dirty = computed(() => editor.value !== null && JSON.stringify(form) !== initialDraft);
function clearEditor() { editor.value = null; original.value = null; form.name = ""; form.endpoint = ""; form.caCertificatePem = ""; requestId = ""; }
function reset() { rows.value = []; total.value = 0; selected.value = null; clearEditor(); reloadRequired.value = false; }
const scope = useCloudSettingsScope(reset, load);
const { busy, error, allowed } = scope;
async function discard() {
  if (directoryPanel.value?.busy || grantPanel.value?.busy) return false;
  if (!dirty.value && !directoryPanel.value?.dirty && !grantPanel.value?.dirty) return true;
  try { await ElMessageBox.confirm(text.value.discard, text.value.discardTitle, { type: "warning", confirmButtonText: text.value.close, cancelButtonText: text.value.cancel }); return true; }
  catch { return false; }
}
async function load(nextPage = page.value) {
  const ticket = scope.begin(); if (!ticket) return;
  try {
    if (!await discard() || !scope.current(ticket)) return;
    clearEditor(); rows.value = []; selected.value = null;
    const result = await fetchCloudConnections({ page: nextPage, pageSize: 10, search: search.value });
    if (!scope.current(ticket)) return;
    rows.value = result.items; total.value = result.total; page.value = result.page; reloadRequired.value = false;
  } catch (cause) { scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function open(mode: NonNullable<typeof editor.value>, row?: CloudConnectionRecord) {
  if (reloadRequired.value || !allowed.value) return;
  const ticket = scope.begin(); if (!ticket) return;
  try {
    if (!await discard() || !scope.current(ticket)) return;
    clearEditor();
    const record = mode === "security" && row ? await fetchCloudConnection(row.id) : row;
    if (!scope.current(ticket)) return;
    original.value = record ?? null;
    Object.assign(form, { name: record?.name ?? "", endpoint: record?.endpoint ?? "", caCertificatePem: record?.caCertificatePem ?? "" });
    requestId = mode === "create" ? crypto.randomUUID() : "";
    initialDraft = JSON.stringify(form); editor.value = mode;
  } catch (cause) { scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
function apply(row: CloudConnectionRecord) {
  const index = rows.value.findIndex(item => item.id === row.id);
  if (index >= 0) rows.value[index] = row;
  else { rows.value = [row, ...rows.value].slice(0, 10); total.value++; }
  if (selected.value?.id === row.id) selected.value = row;
}
async function save() {
  if (busy.value || reloadRequired.value || !editor.value) return;
  const mode = editor.value, target = original.value;
  if ((mode !== "security" && !form.name.trim()) || (mode !== "name" && !form.endpoint.startsWith("https://"))) { error.value = text.value.invalid; return; }
  const ticket = scope.begin(); if (!ticket) return;
  let sent = false;
  try {
    await ElMessageBox.confirm(text.value.confirm, text.value.confirmTitle, { type: "warning", confirmButtonText: text.value.save, cancelButtonText: text.value.cancel });
    if (!scope.current(ticket)) return;
    sent = true;
    const result = mode === "create" ? await createCloudConnection({ ...form, requestId })
      : mode === "name" && target ? await renameCloudConnection(target.id, form.name, target.metadataRevision)
      : target ? await updateCloudConnectionSecurity(target.id, { ...form, expectedSecurityRevision: target.securityRevision }) : null;
    if (scope.current(ticket) && result) { apply(result); clearEditor(); ElMessage.success(text.value.saved); }
  } catch (cause) { if (scope.current(ticket) && sent) reloadRequired.value = true; scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function changeState(row: CloudConnectionRecord, state: CloudSourceState) {
  if (busy.value || reloadRequired.value || editor.value) return;
  const ticket = scope.begin(); if (!ticket) return;
  let sent = false;
  try {
    await ElMessageBox.confirm(formatMessage(text.value.stateConfirm, { state: text.value.states[state] }), text.value.confirmTitle,
      { type: "warning", confirmButtonText: text.value.save, cancelButtonText: text.value.cancel });
    if (!scope.current(ticket)) return;
    sent = true;
    const result = await setCloudConnectionState(row.id, state, row.stateRevision);
    if (scope.current(ticket)) { apply(result); ElMessage.success(text.value.saved); }
  } catch (cause) { if (scope.current(ticket) && sent) reloadRequired.value = true; scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function closeEditor(done?: () => void) {
  const ticket = scope.begin(); if (!ticket) return;
  try { if (await discard() && scope.current(ticket)) { clearEditor(); done?.(); } }
  finally { scope.finish(ticket); }
}
async function select(row: CloudConnectionRecord) {
  const ticket = scope.begin(); if (!ticket) return;
  try { if (await discard() && scope.current(ticket)) { selected.value = row; tab.value = "directories"; } }
  finally { scope.finish(ticket); }
}
async function closeSelected(done?: () => void) {
  const ticket = scope.begin(); if (!ticket) return;
  try { if (await discard() && scope.current(ticket)) { selected.value = null; done?.(); } }
  finally { scope.finish(ticket); }
}
</script>

<template>
  <section class="cloud-connections">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="reloadRequired" :title="text.reloadRequired" type="warning" :closable="false" show-icon />
    <div class="connection-toolbar">
      <h3>{{ text.title }}</h3>
      <el-input v-model="search" :placeholder="text.search" :aria-label="text.search" clearable maxlength="80" :disabled="busy" @keyup.enter="load(1)" @clear="load(1)" />
      <el-button :icon="Refresh" :disabled="busy" @click="load()">{{ text.refresh }}</el-button>
      <el-button :icon="Lock" :disabled="busy" @click="protectionVisible = true">{{ messages.cloudProtections.manage }}</el-button>
      <el-button type="primary" :icon="Plus" :disabled="busy || reloadRequired" @click="open('create')">{{ text.add }}</el-button>
    </div>
    <div class="connection-table">
      <FullscreenTablePanel variant="embedded"><el-table :data="rows" border :empty-text="text.empty" row-key="id">
        <el-table-column prop="name" :label="text.name" min-width="160" show-overflow-tooltip />
        <el-table-column prop="endpoint" :label="text.endpoint" min-width="260" show-overflow-tooltip />
        <el-table-column :label="text.state" width="90"><template #default="{ row }"><el-tag :type="row.state === 'enabled' ? 'success' : 'info'">{{ text.states[row.state as CloudSourceState] }}</el-tag></template></el-table-column>
        <el-table-column :label="text.operations" fixed="right" width="196" align="center">
          <template #default="{ row }"><div class="table-actions">
            <el-tooltip :content="text.select" :trigger-keys="[]"><el-button text class="table-action-button action-view" :icon="FolderOpened" :aria-label="text.select" :disabled="busy" @click="select(row)" /></el-tooltip>
            <el-tooltip :content="text.editName" :trigger-keys="[]"><el-button text class="table-action-button action-edit" :icon="Edit" :aria-label="text.editName" :disabled="busy || reloadRequired" @click="open('name', row)" /></el-tooltip>
            <el-tooltip :content="text.security" :trigger-keys="[]"><el-button text class="table-action-button action-edit" :icon="Lock" :aria-label="text.security" :disabled="busy || reloadRequired" @click="open('security', row)" /></el-tooltip>
            <el-tooltip :content="row.state === 'enabled' ? text.disable : text.enable" :trigger-keys="[]"><el-button text class="table-action-button action-toggle" :icon="row.state === 'enabled' ? CircleClose : CircleCheck" :aria-label="row.state === 'enabled' ? text.disable : text.enable" :disabled="busy || reloadRequired" @click="changeState(row, row.state === 'enabled' ? 'disabled' : 'enabled')" /></el-tooltip>
            <el-tooltip :content="text.archive" :trigger-keys="[]"><el-button text class="table-action-button action-delete" :icon="Box" :aria-label="text.archive" :disabled="busy || reloadRequired || row.state === 'archived'" @click="changeState(row, 'archived')" /></el-tooltip>
          </div></template>
        </el-table-column>
      </el-table>
        <el-pagination :current-page="page" :page-size="10" :total="total" :pager-count="5" layout="total, prev, pager, next" :disabled="busy" @current-change="load" />
      </FullscreenTablePanel>
    </div>
    <el-drawer :model-value="selected !== null" :title="text.details" size="min(860px, 100vw)" class="cloud-connection-workspace" append-to-body
      :before-close="closeSelected" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close>
      <template v-if="selected" #header>
        <div class="connection-workspace-heading"><span>{{ text.selected }}</span><h3>{{ selected.name }}</h3></div>
      </template>
      <template v-if="selected">
      <el-tabs v-model="tab" class="connection-workspace-tabs">
        <el-tab-pane :label="text.directories" name="directories" lazy><CloudDirectoryManager ref="directoryPanel" :key="`${selected.id}:${selected.securityRevision}:${selected.stateRevision}`" :connection-id="selected.id" /></el-tab-pane>
        <el-tab-pane :label="text.grants" name="grants" lazy><CloudConnectionGrantEditor ref="grantPanel" :key="selected.id" :connection-id="selected.id" /></el-tab-pane>
      </el-tabs>
      </template>
      <template #footer><el-button :disabled="busy" @click="closeSelected()">{{ text.close }}</el-button></template>
    </el-drawer>
    <CloudGlobalPolicyPanel />
    <CloudProtectionDialog v-if="protectionVisible" v-model:visible="protectionVisible" managed />
    <el-dialog :model-value="editor !== null" :title="editor === 'create' ? text.add : editor === 'name' ? text.editName : text.security" width="min(640px, calc(100vw - 32px))" class="cloud-connection-dialog" append-to-body :before-close="closeEditor" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-alert v-if="reloadRequired" :title="text.reloadRequired" type="warning" :closable="false" show-icon />
      <el-form label-position="top" :disabled="busy || reloadRequired" @submit.prevent="save">
        <el-form-item v-if="editor !== 'security'" :label="text.name" required><el-input v-model="form.name" maxlength="80" /></el-form-item>
        <template v-if="editor !== 'name'">
          <el-form-item :label="settingsText.endpoint" required>
            <el-input v-model="form.endpoint" :readonly="editor === 'security'" maxlength="4096" autocomplete="off" />
            <p v-if="editor === 'security'" class="connection-help">{{ text.endpointFixed }}</p>
          </el-form-item>
          <el-form-item :label="settingsText.ca"><el-input v-model="form.caCertificatePem" type="textarea" :rows="5" maxlength="16384" spellcheck="false" /><p class="connection-help">{{ settingsText.caHint }}</p></el-form-item>
        </template>
      </el-form>
      <template #footer><el-button :disabled="busy" @click="closeEditor()">{{ text.cancel }}</el-button><el-button type="primary" :loading="busy" :disabled="reloadRequired || !dirty" @click="save">{{ text.save }}</el-button></template>
    </el-dialog>
  </section>
</template>

<style scoped>
.cloud-connections { display: grid; gap: 16px; min-width: 0; }
.connection-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; min-width: 0; }
.connection-toolbar h3 { margin: 0 auto 0 0; font-size: 16px; }
.connection-toolbar .el-input { width: 220px; max-width: 100%; }
.connection-toolbar :deep(.el-button + .el-button) { margin-left: 0; }
.connection-table { width: 100%; min-width: 0; overflow: hidden; }
.connection-workspace-heading { display: grid; gap: 6px; min-width: 0; }
.connection-workspace-heading > span { color: var(--text-muted); font-size: 13px; }
.connection-workspace-heading h3 { margin: 0; font-size: 16px; line-height: 1.5; overflow-wrap: anywhere; }
.connection-workspace-tabs { min-width: 0; }
.connection-workspace-tabs :deep(.el-tabs__content) { overflow: visible; }
.connection-help { margin: 6px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.cloud-connections :deep(.el-tabs__content) { min-width: 0; overflow: visible; }
.cloud-connections :deep(.el-pagination) { max-width: 100%; flex-wrap: wrap; gap: 4px; }
</style>
