<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Check, Refresh } from "@element-plus/icons-vue";
import { fetchUsers, type AuthUser } from "../api/client";
import { fetchCloudDirectoryProfiles, fetchCloudUserGrant, saveCloudUserGrant,
  type CloudDirectoryProfile, type CloudUserGrant } from "../api/cloudConnections";
import { formatMessage } from "../locales/zh-CN";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { useCloudSettingsScope } from "../utils/useCloudSettingsScope";

const props = defineProps<{ connectionId: string }>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudConnections);
const users = ref<Pick<AuthUser, "id" | "username" | "displayName" | "enabled">[]>([]);
const directories = ref<CloudDirectoryProfile[]>([]), total = ref(0), page = ref(1), search = ref("");
const userId = ref<number | null>(null), snapshot = ref<CloudUserGrant | null>(null), reloadRequired = ref(false);
const form = reactive({ allowed: false, directoryProfileIds: [] as string[] });
const effectiveIds = computed(() => form.allowed ? [...form.directoryProfileIds].sort() : []);
const dirty = computed(() => snapshot.value !== null && (form.allowed !== snapshot.value.allowed
  || JSON.stringify(effectiveIds.value) !== JSON.stringify([...snapshot.value.directoryProfileIds].sort())));
const scope = useCloudSettingsScope(reset, load), { busy, error } = scope;
function reset() { users.value = []; directories.value = []; total.value = 0; userId.value = null; snapshot.value = null; form.allowed = false; form.directoryProfileIds = []; reloadRequired.value = false; }
async function discard() {
  if (!dirty.value) return true;
  try { await ElMessageBox.confirm(text.value.discard, text.value.discardTitle, { type: "warning", confirmButtonText: text.value.close, cancelButtonText: text.value.cancel }); return true; }
  catch { return false; }
}
async function load() {
  if (busy.value) return;
  const ticket = scope.begin(); if (!ticket) return;
  try {
    if (!await discard() || !scope.current(ticket)) return;
    snapshot.value = null; userId.value = null; form.allowed = false; form.directoryProfileIds = []; users.value = []; directories.value = [];
    const result = await fetchUsers();
    if (!scope.current(ticket)) return;
    users.value = result.items.map(({ id, username, displayName, enabled }) => ({ id, username, displayName, enabled }));
    const profiles = await fetchCloudDirectoryProfiles(props.connectionId, { page: 1, pageSize: 10, search: search.value });
    if (scope.current(ticket)) { directories.value = profiles.items; total.value = profiles.total; page.value = profiles.page; reloadRequired.value = false; }
  } catch (cause) { scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function chooseUser(nextId: number | null) {
  const ticket = scope.begin(); if (!ticket) return;
  try {
    if (!await discard() || !scope.current(ticket)) return;
    userId.value = nextId; snapshot.value = null; form.allowed = false; form.directoryProfileIds = [];
    if (nextId === null) return;
    const result = await fetchCloudUserGrant(props.connectionId, nextId);
    if (scope.current(ticket) && result.connectionId === props.connectionId && result.userId === nextId) {
      snapshot.value = result; form.allowed = result.allowed; form.directoryProfileIds = [...result.directoryProfileIds]; reloadRequired.value = false;
    }
  } catch (cause) { scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function loadPage(nextPage: number) {
  const ticket = scope.begin(); if (!ticket) return;
  directories.value = [];
  try {
    const result = await fetchCloudDirectoryProfiles(props.connectionId, { page: nextPage, pageSize: 10, search: search.value });
    if (scope.current(ticket)) { directories.value = result.items; total.value = result.total; page.value = result.page; }
  } catch (cause) { scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
function selectPage(checked: boolean) {
  if (busy.value || !snapshot.value || !form.allowed || reloadRequired.value) return;
  const selected = new Set(form.directoryProfileIds);
  for (const directory of directories.value) {
    if (!checked) selected.delete(directory.id);
    else if (directory.state !== "archived") selected.add(directory.id);
  }
  form.directoryProfileIds = [...selected];
}
async function save() {
  if (!snapshot.value || !dirty.value || reloadRequired.value) return;
  const ticket = scope.begin(); if (!ticket) return;
  const target = snapshot.value, allowed = form.allowed, directoryProfileIds = [...effectiveIds.value]; let sent = false;
  try {
    await ElMessageBox.confirm(text.value.confirm, text.value.confirmTitle, { type: "warning", confirmButtonText: text.value.save, cancelButtonText: text.value.cancel });
    if (!scope.current(ticket)) return;
    sent = true;
    const result = await saveCloudUserGrant(props.connectionId, target.userId, { allowed, directoryProfileIds, expectedGrantRevision: target.grantRevision });
    if (scope.current(ticket) && result.connectionId === props.connectionId && result.userId === target.userId) {
      snapshot.value = result; form.allowed = result.allowed; form.directoryProfileIds = [...result.directoryProfileIds]; ElMessage.success(text.value.saved);
    }
  } catch (cause) { if (scope.current(ticket) && sent) reloadRequired.value = true; scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
function userChanged(value: unknown) { if (typeof value === "number" || value === null || value === "") void chooseUser(value === "" ? null : value); }
defineExpose({ dirty, busy });
</script>
<template>
  <section class="cloud-user-grants">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="reloadRequired" :title="text.reloadRequired" type="warning" :closable="false" show-icon />
    <div class="grant-toolbar"><el-select :model-value="userId" :placeholder="text.chooseUser" :aria-label="text.user" filterable clearable :disabled="busy" @update:model-value="userChanged">
      <el-option v-for="user in users" :key="user.id" :value="user.id" :label="`${user.displayName || user.username} (${user.username})${user.enabled ? '' : text.disabledUser}`" />
    </el-select><el-button :icon="Refresh" :disabled="busy" @click="load">{{ text.refresh }}</el-button></div>
    <p class="grant-help">{{ text.grantHint }}</p>
    <el-checkbox v-model="form.allowed" :disabled="busy || !snapshot || reloadRequired">{{ text.allowConnection }}</el-checkbox>
    <h4>{{ text.directoryWhitelist }}</h4>
    <div class="grant-toolbar"><el-input v-model="search" :placeholder="text.search" :aria-label="text.search" maxlength="80" clearable :disabled="busy" @keyup.enter="loadPage(1)" @clear="loadPage(1)" />
      <el-button :disabled="busy || !snapshot || !form.allowed || reloadRequired" @click="selectPage(true)">{{ text.selectPage }}</el-button>
      <el-button :disabled="busy || !snapshot || !form.allowed || reloadRequired" @click="selectPage(false)">{{ text.clearPage }}</el-button>
    </div>
    <el-checkbox-group v-model="form.directoryProfileIds" :disabled="busy || !snapshot || !form.allowed || reloadRequired" class="grant-directories">
      <el-checkbox v-for="directory in directories" :key="directory.id" :value="directory.id" :disabled="directory.state === 'archived' && !form.directoryProfileIds.includes(directory.id)">
        <span class="grant-directory-name">{{ directory.name }} · {{ text.states[directory.state] }}</span><span class="grant-directory-path">{{ directory.approvedRoot }}</span>
      </el-checkbox>
    </el-checkbox-group>
    <el-empty v-if="!busy && directories.length === 0" :description="text.directoryEmpty" :image-size="60" />
    <el-pagination :current-page="page" :page-size="10" :total="total" :pager-count="5" layout="total, prev, pager, next" :disabled="busy" @current-change="loadPage" />
    <p class="grant-help">{{ formatMessage(text.selectedCount, { count: effectiveIds.length }) }}</p>
    <el-button type="primary" :icon="Check" :loading="busy" :disabled="!snapshot || !dirty || reloadRequired" @click="save">{{ text.save }}</el-button>
  </section>
</template>
<style scoped>
.cloud-user-grants { display: grid; gap: 12px; min-width: 0; }
.cloud-user-grants h4 { font-size: 14px; margin: 0; }
.grant-toolbar { display: flex; flex-wrap: wrap; gap: 10px; min-width: 0; }
.grant-toolbar .el-select, .grant-toolbar .el-input { width: 260px; max-width: 100%; }
.grant-toolbar :deep(.el-button + .el-button) { margin-left: 0; }
.grant-help { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.grant-directories { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; min-width: 0; }
.grant-directories :deep(.el-checkbox) { margin: 0; height: auto; min-height: 40px; min-width: 0; align-items: flex-start; }
.grant-directories :deep(.el-checkbox__input) { margin-top: 4px; }
.grant-directories :deep(.el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; min-width: 0; }
.grant-directory-name, .grant-directory-path { display: block; }
.grant-directory-path { color: var(--text-subtle); font-size: 13px; }
.cloud-user-grants > .el-button { justify-self: start; }
.cloud-user-grants :deep(.el-pagination) { max-width: 100%; flex-wrap: wrap; gap: 4px; }
@media (max-width: 850px) { .grant-directories { grid-template-columns: minmax(0, 1fr); } }
</style>
