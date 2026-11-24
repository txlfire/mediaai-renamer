<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Check, Refresh } from "@element-plus/icons-vue";
import { fetchCloudGlobalPolicy, saveCloudGlobalPolicy, type CloudGlobalPolicy } from "../api/cloudConnections";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { useCloudSettingsScope } from "../utils/useCloudSettingsScope";
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudConnections), settingsText = computed(() => messages.value.cloudDriveSettings);
const snapshot = ref<CloudGlobalPolicy | null>(null), reloadRequired = ref(false);
const form = reactive({ minIntervalSeconds: 2.2, failureCooldownSeconds: 300 });
const dirty = computed(() => snapshot.value !== null && (form.minIntervalSeconds !== snapshot.value.minIntervalSeconds || form.failureCooldownSeconds !== snapshot.value.failureCooldownSeconds));
function reset() { snapshot.value = null; reloadRequired.value = false; Object.assign(form, { minIntervalSeconds: 2.2, failureCooldownSeconds: 300 }); }
const scope = useCloudSettingsScope(reset, load), { busy, error } = scope;
async function load() {
  const ticket = scope.begin(); if (!ticket) return;
  try {
    if (dirty.value) await ElMessageBox.confirm(text.value.discard, text.value.discardTitle, { confirmButtonText: text.value.close, cancelButtonText: text.value.cancel });
    if (!scope.current(ticket)) return;
    const result = await fetchCloudGlobalPolicy();
    if (scope.current(ticket)) { snapshot.value = result; Object.assign(form, result); reloadRequired.value = false; }
  } catch (cause) { scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
async function save() {
  if (!snapshot.value || reloadRequired.value || !dirty.value) return;
  const ticket = scope.begin(); if (!ticket) return;
  const revision = snapshot.value.revision; let sent = false;
  try {
    await ElMessageBox.confirm(text.value.confirm, text.value.confirmTitle, { type: "warning", confirmButtonText: text.value.save, cancelButtonText: text.value.cancel });
    if (!scope.current(ticket)) return;
    sent = true;
    const result = await saveCloudGlobalPolicy(form.minIntervalSeconds, form.failureCooldownSeconds, revision);
    if (scope.current(ticket)) { snapshot.value = result; Object.assign(form, result); ElMessage.success(text.value.saved); }
  } catch (cause) { if (scope.current(ticket) && sent) reloadRequired.value = true; scope.fail(ticket, cause); }
  finally { scope.finish(ticket); }
}
</script>
<template>
  <section class="cloud-global-policy">
    <h3>{{ text.policy }}</h3><p class="policy-help">{{ text.policyHint }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="reloadRequired" :title="text.reloadRequired" type="warning" :closable="false" show-icon />
    <el-form label-position="top" :disabled="busy || !snapshot || reloadRequired" @submit.prevent="save"><div class="policy-fields">
      <el-form-item :label="settingsText.interval"><el-input-number v-model="form.minIntervalSeconds" :min="2" :max="300" :precision="1" controls-position="right" /></el-form-item>
      <el-form-item :label="settingsText.cooldown"><el-input-number v-model="form.failureCooldownSeconds" :min="60" :max="86400" :precision="0" controls-position="right" /></el-form-item>
    </div></el-form>
    <div class="policy-actions"><el-button type="primary" :icon="Check" :loading="busy" :disabled="!snapshot || reloadRequired || !dirty" @click="save">{{ text.save }}</el-button><el-button :icon="Refresh" :disabled="busy" @click="load">{{ text.refresh }}</el-button></div>
  </section>
</template>
<style scoped>
.cloud-global-policy { min-width: 0; border-top: 1px solid var(--el-border-color-light); padding-top: 16px; }
.cloud-global-policy h3 { font-size: 16px; margin: 0; }
.policy-help { margin: 8px 0 16px; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.policy-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; max-width: 680px; }
.policy-fields :deep(.el-input-number) { width: 100%; }
.policy-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.policy-actions :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 720px) { .policy-fields { grid-template-columns: minmax(0, 1fr); } }
</style>
