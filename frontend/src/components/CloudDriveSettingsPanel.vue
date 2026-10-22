<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Check, InfoFilled, Refresh } from "@element-plus/icons-vue";
import { fetchCloudDriveSettings, saveCloudDriveSettings, type CloudDriveSettingsValues, type CloudDriveSettingsSnapshot } from "../api/cloudDriveSettings";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useAuthStore } from "../stores/auth";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";

const text = messages.cloudDriveSettings;
const auth = useAuthStore();
const allowed = computed(() => auth.hasPermission("settings:write"));
const snapshot = ref<CloudDriveSettingsSnapshot | null>(null);
const busy = ref(false);
const error = ref("");
const form = reactive<CloudDriveSettingsValues>({ endpoint: "", approvedRoot: "", caCertificatePem: "",
  minIntervalSeconds: 2, failureCooldownSeconds: 300, webExecutionEnabled: false,
  directoryRenameEnabled: false, directoryMappingRevision: "", quarantineEnabled: false, quarantineFolderName: "_MediaAI_Quarantine" });
let version = 0;
let active = true;
function current(requestVersion: number, session: number) {
  return active && requestVersion === version && session === getSessionGeneration() && allowed.value;
}
function accept(result: CloudDriveSettingsSnapshot) { snapshot.value = result; Object.assign(form, result.values); }
async function load() {
  if (!allowed.value || busy.value) return;
  const requestVersion = ++version, session = getSessionGeneration();
  busy.value = true; error.value = ""; snapshot.value = null;
  try { const result = await fetchCloudDriveSettings(); if (current(requestVersion, session)) accept(result); }
  catch (cause) { if (current(requestVersion, session) && !isSessionChangedError(cause)) error.value = text.failed; }
  finally { if (current(requestVersion, session)) busy.value = false; }
}
async function save() {
  if (!allowed.value || busy.value || !snapshot.value || snapshot.value.changeBlocked) return;
  const requestVersion = ++version, session = getSessionGeneration();
  const revision = snapshot.value.revision;
  busy.value = true; error.value = "";
  try {
    await ElMessageBox.confirm(text.confirm, text.confirmTitle, { type: "warning", confirmButtonText: text.save, cancelButtonText: text.cancel });
    if (!current(requestVersion, session)) return;
    const result = await saveCloudDriveSettings({ ...form }, revision);
    if (current(requestVersion, session)) { accept(result); ElMessage.success(text.saved); }
  } catch (cause) {
    if (current(requestVersion, session) && cause !== "cancel" && cause !== "close" && !isSessionChangedError(cause)) {
      error.value = cause instanceof Error ? cause.message : text.failed;
      snapshot.value = null;
    }
  } finally { if (current(requestVersion, session)) busy.value = false; }
}
watch(() => [allowed.value, auth.currentUser?.id], () => {
  version++; busy.value = false; snapshot.value = null; form.caCertificatePem = "";
  if (allowed.value) void load();
});
onMounted(load);
onBeforeUnmount(() => { active = false; version++; form.caCertificatePem = ""; });
</script>

<template>
  <div class="cloud-settings settings-form">
    <div class="cloud-settings-hint"><el-icon><InfoFilled /></el-icon><span>{{ text.hint }}</span></div>
    <el-alert v-if="!allowed" :title="text.permission" type="warning" :closable="false" />
    <template v-else>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-alert v-if="snapshot?.changeBlocked" :title="text.blocked" type="warning" :closable="false" show-icon />
      <el-form label-position="top" :disabled="busy || !snapshot || snapshot.changeBlocked" @submit.prevent="save">
        <div class="cloud-settings-grid">
          <el-form-item :label="text.endpoint"><el-input v-model="form.endpoint" :placeholder="text.endpointPlaceholder" maxlength="4096" autocomplete="off" /></el-form-item>
          <el-form-item :label="text.root"><el-input v-model="form.approvedRoot" :placeholder="text.rootPlaceholder" maxlength="4096" /></el-form-item>
          <el-form-item :label="text.interval"><el-input-number v-model="form.minIntervalSeconds" :min="2" :max="300" :precision="1" controls-position="right" /></el-form-item>
          <el-form-item :label="text.cooldown"><el-input-number v-model="form.failureCooldownSeconds" :min="60" :max="86400" :precision="0" controls-position="right" /></el-form-item>
          <el-form-item :label="text.mapping"><el-input v-model="form.directoryMappingRevision" maxlength="64" /><p class="cloud-settings-help">{{ text.mappingHint }}</p></el-form-item>
          <el-form-item :label="text.quarantineFolder"><el-input v-model="form.quarantineFolderName" maxlength="240" /></el-form-item>
        </div>
        <el-form-item :label="text.ca"><el-input v-model="form.caCertificatePem" type="textarea" :rows="5" maxlength="16384" spellcheck="false" /><p class="cloud-settings-help">{{ text.caHint }}</p></el-form-item>
        <div class="cloud-settings-switches">
          <el-checkbox v-model="form.webExecutionEnabled">{{ text.web }}</el-checkbox>
          <el-checkbox v-model="form.directoryRenameEnabled">{{ text.rename }}</el-checkbox>
          <el-checkbox v-model="form.quarantineEnabled">{{ text.quarantine }}</el-checkbox>
        </div>
        <p class="cloud-settings-help">{{ text.limits }}</p>
        <p class="cloud-settings-help">{{ text.writeRisk }}</p>
      </el-form>
      <div v-if="snapshot" class="cloud-settings-meta"><span>{{ text.revision }}: {{ snapshot.revision }}</span><span v-if="snapshot.caSha256">{{ text.fingerprint }}: {{ snapshot.caSha256 }}</span></div>
      <div class="cloud-settings-actions">
        <el-button type="primary" :icon="Check" :loading="busy" :disabled="busy || !snapshot || snapshot.changeBlocked" @click="save">{{ text.save }}</el-button>
        <el-button :icon="Refresh" :disabled="busy" @click="load">{{ text.refresh }}</el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cloud-settings { min-width: 0; display: grid; gap: 16px; }
.cloud-settings-hint { display: flex; align-items: flex-start; gap: 8px; padding: 10px 12px; border: 1px solid var(--el-border-color-light); border-radius: 6px; background: var(--el-fill-color-light); color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.cloud-settings-hint .el-icon { color: var(--el-color-primary); flex-shrink: 0; margin-top: 4px; }
.cloud-settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 20px; }
.cloud-settings :deep(.el-form-item__content) { min-width: 0; }
.cloud-settings :deep(.el-input-number) { width: 100%; }
.cloud-settings-help { width: 100%; margin: 6px 0 0; font-size: 13px; line-height: 1.6; color: var(--el-text-color-secondary); overflow-wrap: anywhere; }
.cloud-settings-switches { display: flex; flex-wrap: wrap; gap: 8px 20px; }
.cloud-settings-switches :deep(.el-checkbox) { margin: 0; max-width: 100%; height: auto; min-height: 32px; }
.cloud-settings-switches :deep(.el-checkbox__label) { white-space: normal; }
.cloud-settings-meta { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 12px; color: var(--el-text-color-secondary); overflow-wrap: anywhere; min-width: 0; }
.cloud-settings-meta span { min-width: 0; }
.cloud-settings-actions { display: flex; flex-wrap: wrap; gap: 12px; }
.cloud-settings-actions :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 1000px) { .cloud-settings-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
