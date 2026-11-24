<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Check, CircleClose, Connection, InfoFilled, Refresh, Search } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";
import * as api from "../api/cloudDiagnostics";
import { CloudConnectionRequestError } from "../api/cloudConnections";
import { inventoryRequestId } from "../api/cloudInventory";
import { useAuthStore } from "../stores/auth";
import { getSessionGeneration } from "../utils/sessionGeneration";
import { resolveCloudTaskBinding, sameCloudTaskSource } from "../utils/cloudTaskBinding";
import type { CloudSourceTicket } from "../utils/cloudSourceDraft";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import CloudSourcePicker from "./CloudSourcePicker.vue";
import CloudTaskSourceSummary from "./CloudTaskSourceSummary.vue";
import CloudProtectionDialog from "./CloudProtectionDialog.vue";

const props = defineProps<{ visible: boolean; requestId: string; persistRequestId: (id: string) => Promise<void> }>();
const emit = defineEmits<{ "update:visible": [value: boolean] }>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudDiagnostics), auth = useAuthStore();
const ownerSession = getSessionGeneration(), ownerId = auth.currentUser?.id;
const stage = ref<api.CloudDiagnosticStage>("tls"), tokenValue = ref(""), confirmed = ref(false);
const sourceTicket = ref<CloudSourceTicket | null>(null);
const picker = ref<{ isCurrent: (ticket: CloudSourceTicket) => boolean } | null>(null);
const policy = ref<api.CloudDiagnosticPolicy | null>(null), result = ref<api.CloudDiagnosticResult | null>(null);
const requestId = ref(inventoryRequestId(props.requestId)), errorText = ref("");
const busy = ref(false), policyLoading = ref(false), querying = ref(false);
const expectedReceipt = ref<api.CloudDiagnosticReceipt | null>(null);
const protectionVisible = ref(false);
let active = true, sequence = 0, automaticReads = 0;
let pollTimer: ReturnType<typeof setTimeout> | undefined;
const handoff = { apiToken: "" };
const permitted = computed(() => auth.hasPermission("scan:run"));
function trustedTransport() { return typeof location !== "undefined" && location.protocol === "https:"; }
const secure = computed(trustedTransport);
const validToken = computed(() => /^[\x21-\x7e]{1,8192}$/.test(tokenValue.value));
function ownerValid() {
  return active && props.visible && permitted.value && ownerSession === getSessionGeneration()
    && ownerId === auth.currentUser?.id && (typeof document === "undefined" || !document.hidden);
}
function current(value: number) { return ownerValid() && value === sequence; }
const canSubmit = computed(() => Boolean(ownerValid() && secure.value && !requestId.value && !busy.value && !querying.value
  && !policyLoading.value && confirmed.value && policy.value?.available && sourceTicket.value
  && picker.value?.isCurrent(sourceTicket.value) && (stage.value === "tls" || validToken.value)));
const canFinish = computed(() => Boolean(result.value && !busy.value && !querying.value && !result.value.workerSlotHeld && !result.value.unresolved
  && ["completed", "cancelled", "interrupted", "review"].includes(result.value.status)));
const phasePassed = computed(() => Boolean(result.value?.status === "completed" && !result.value.workerSlotHeld && !result.value.unresolved
  && result.value.tlsState === "passed" && (result.value.stage === "tls" || result.value.tokenState === "passed" && result.value.runtimeState === "passed")));
const resultReason = computed(() => result.value?.reasonCode
  ? text.value.reasons[result.value.reasonCode as keyof typeof text.value.reasons] ?? text.value.phaseFailed : "");

function purge() {
  clearTimeout(pollTimer); automaticReads = 0;
  sequence++; handoff.apiToken = ""; tokenValue.value = ""; confirmed.value = false;
  policy.value = null; sourceTicket.value = null; result.value = null; expectedReceipt.value = null;
  busy.value = false; querying.value = false; policyLoading.value = false;
}
function sourceChanged(value: CloudSourceTicket | null) {
  if (requestId.value) return;
  purge();
  sourceTicket.value = value;
}
function closeDialog() { purge(); emit("update:visible", false); }
function fixedLimits(value: api.CloudDiagnosticPolicy["limits"] | undefined) {
  return value?.rpc === 2 && value.seconds === 30 && value.bytes === 65536;
}
function receiptValid(value: api.CloudDiagnosticReceipt) {
  if (!value || value.requestId !== requestId.value || !inventoryRequestId(value.id) || value.resourceId !== value.id
      || !["diagnostic_tls", "diagnostic_token_runtime"].includes(value.resourceKind)
      || !["accepted", "running", "completed", "interrupted", "cancelled", "review"].includes(value.status)
      || !Number.isSafeInteger(value.revision) || value.revision < 0 || typeof value.replayed !== "boolean") return false;
  try { return resolveCloudTaskBinding(value).mode === "bound"; } catch { return false; }
}
function acceptResult(value: api.CloudDiagnosticResult) {
  const previous = expectedReceipt.value;
  if (!receiptValid(value) || value.resourceKind !== (value.stage === "tls" ? "diagnostic_tls" : value.stage === "token-runtime" ? "diagnostic_token_runtime" : "")
      || value.networkAccess !== false || value.writeAvailable !== false || value.executionAvailable !== false || !fixedLimits(value.limits)
      || !["not_run", "passed", "failed"].includes(value.tlsState)
      || !["not_run", "passed", "failed", "not_provided"].includes(value.tokenState)
      || !["not_run", "passed", "failed", "permission_missing", "unsupported"].includes(value.runtimeState)
      || typeof value.workerSlotHeld !== "boolean" || typeof value.unresolved !== "boolean"
      || ![value.usedRpc, value.receivedBytes].every(n => Number.isSafeInteger(n) && n >= 0)
      || [value.coreBuild, value.cloudApiBuild].some(v => v !== null && (typeof v !== "string" || v.length > 256 || /[\x00-\x1f]/.test(v)))
      || previous && (value.id !== previous.id || value.contextId !== previous.contextId || value.revision < previous.revision || value.resourceKind !== previous.resourceKind || !sameCloudTaskSource(value, previous.source))) {
    throw new Error("invalid_diagnostic_result");
  }
  result.value = value; expectedReceipt.value = value;
  schedulePoll();
}
function schedulePoll() {
  clearTimeout(pollTimer);
  const value = result.value;
  // 仅读本地状态；结果未知、隐藏或达到20次上限后改由用户明确刷新。
  if (!ownerValid() || !value || value.unresolved || automaticReads >= 20
      || !(["accepted", "running"].includes(value.status) || value.workerSlotHeld)) return;
  pollTimer = setTimeout(() => { automaticReads++; void lookup(true); }, 1500);
}
async function loadPolicy() {
  if (!ownerValid() || requestId.value || busy.value || policyLoading.value || querying.value) return;
  const ticket = sourceTicket.value;
  if (!ticket || !picker.value?.isCurrent(ticket)) { errorText.value = messages.value.cloudSourcePicker.missingSource; return; }
  tokenValue.value = ""; confirmed.value = false; policy.value = null; errorText.value = "";
  const version = ++sequence, selectedStage = stage.value;
  policyLoading.value = true;
  try {
    const value = await api.fetchCloudDiagnosticPolicy(ticket.source.connectionId, ticket.source.directoryProfileId, ticket.source.selectedPath, selectedStage);
    if (!current(version) || !picker.value?.isCurrent(ticket) || stage.value !== selectedStage) return;
    if (!value || value.connectionId !== ticket.source.connectionId || value.directoryProfileId !== ticket.source.directoryProfileId
        || value.selectedPath !== ticket.source.selectedPath || value.networkAccess !== false || !fixedLimits(value.limits)
        || !/^[a-f0-9]{64}$/.test(value.policyHash) || typeof value.available !== "boolean"
        || !Object.keys(ticket.revisions).every(key => value.revisions?.[key as keyof typeof ticket.revisions] === ticket.revisions[key as keyof typeof ticket.revisions])) {
      throw new Error("invalid_diagnostic_policy");
    }
    policy.value = value;
    if (!value.available) errorText.value = text.value.policyBlocked;
  } catch (cause) {
    if (current(version)) errorText.value = cause instanceof CloudConnectionRequestError && cause.reasonCode === "storage_not_migrated"
      ? text.value.reasons.storage_not_migrated : text.value.policyFailed;
  } finally { if (current(version)) policyLoading.value = false; }
}
async function submit() {
  if (!ownerValid() || !trustedTransport() || !canSubmit.value || !sourceTicket.value || !policy.value) return;
  const ticket = sourceTicket.value, selectedStage = stage.value, hash = policy.value.policyHash;
  handoff.apiToken = tokenValue.value; tokenValue.value = ""; confirmed.value = false;
  const version = ++sequence;
  busy.value = true; errorText.value = "";
  let input: api.CloudDiagnosticInput | null = null;
  try {
    requestId.value = inventoryRequestId(crypto.randomUUID());
    if (!requestId.value) throw new Error("request_id_unavailable");
    // 先保留原编号；响应丢失后只查本地原意图，不再次提交令牌。
    await props.persistRequestId(requestId.value);
    if (!current(version) || !picker.value?.isCurrent(ticket)) return;
    const common = { ...ticket.source, revisions: ticket.revisions, requestId: requestId.value, policyHash: hash, confirmNetwork: true as const };
    input = selectedStage === "tls" ? { ...common, stage: "tls" } : { ...common, stage: "token-runtime", apiToken: handoff.apiToken };
    const pending = api.submitCloudDiagnostic(input);
    handoff.apiToken = "";
    if (input.stage === "token-runtime") input.apiToken = "";
    const receipt = await pending;
    if (!current(version)) return;
    if (!receiptValid(receipt) || receipt.replayed || receipt.networkAccess !== null || receipt.operationState !== "accepted"
        || receipt.resourceKind !== (selectedStage === "tls" ? "diagnostic_tls" : "diagnostic_token_runtime") || !sameCloudTaskSource(receipt, ticket.source)) {
      throw new Error("invalid_diagnostic_receipt");
    }
    expectedReceipt.value = receipt; errorText.value = text.value.accepted;
    const value = await api.fetchCloudDiagnostic(receipt.id);
    if (current(version)) { acceptResult(value); errorText.value = ""; }
  } catch {
    if (current(version)) errorText.value = requestId.value ? text.value.unknown : text.value.invalid;
  } finally {
    handoff.apiToken = "";
    if (input?.stage === "token-runtime") input.apiToken = "";
    if (current(version)) busy.value = false;
  }
}
async function lookup(automatic = false) {
  if (!ownerValid() || !requestId.value || busy.value || querying.value) return;
  clearTimeout(pollTimer);
  if (!automatic) automaticReads = 0;
  const version = ++sequence;
  tokenValue.value = ""; querying.value = true; errorText.value = ""; result.value = null;
  try {
    const value = await api.findCloudDiagnosticRequest(requestId.value);
    if (current(version)) acceptResult(value);
  } catch (cause) {
    if (current(version)) errorText.value = cause instanceof CloudConnectionRequestError && cause.status === 404 ? text.value.notFound : text.value.failed;
  } finally { if (current(version)) querying.value = false; }
}
async function stop() {
  if (!ownerValid() || !result.value || busy.value || querying.value || !["accepted", "running"].includes(result.value.status)) return;
  const version = ++sequence, original = result.value;
  busy.value = true; tokenValue.value = "";
  try {
    await ElMessageBox.confirm(text.value.stopConfirm, text.value.stopTitle, { type: "warning", confirmButtonText: text.value.stop, cancelButtonText: messages.value.common.cancel });
    if (!current(version)) return;
    const value = await api.cancelCloudDiagnostic(original.id);
    if (current(version)) acceptResult(value);
  } catch (cause) { if (current(version) && cause !== "cancel" && cause !== "close") errorText.value = text.value.unknown; }
  finally { if (current(version)) busy.value = false; }
}
async function finish() {
  if (!ownerValid() || !canFinish.value) return;
  const previous = requestId.value, version = ++sequence;
  busy.value = true; requestId.value = "";
  try {
    await props.persistRequestId("");
    if (current(version)) { purge(); errorText.value = ""; emit("update:visible", false); }
  } catch { if (current(version)) { requestId.value = previous; busy.value = false; errorText.value = text.value.failed; } }
}
function hidden() { if (document.hidden) purge(); }
function expire() { purge(); errorText.value = messages.value.cloudInventory.startFlow.sessionChanged; }
watch(stage, () => { const ticket = sourceTicket.value; purge(); if (!requestId.value) sourceTicket.value = ticket; }, { flush: "sync" });
watch(() => props.requestId, value => { const id = inventoryRequestId(value); if (id !== requestId.value) { purge(); requestId.value = id; } }, { flush: "sync" });
watch(() => props.visible, (value, wasVisible) => {
  purge(); errorText.value = "";
  if (value && wasVisible === false && requestId.value) void lookup();
}, { immediate: true, flush: "sync" });
watch(() => [auth.currentUser?.id, permitted.value], () => { if (!ownerValid()) expire(); }, { flush: "sync" });
onMounted(() => {
  document.addEventListener("visibilitychange", hidden);
  window.addEventListener("pagehide", purge); window.addEventListener("mediaai:auth-expired", expire);
});
onBeforeUnmount(() => {
  active = false; purge(); document.removeEventListener("visibilitychange", hidden);
  window.removeEventListener("pagehide", purge); window.removeEventListener("mediaai:auth-expired", expire);
});
</script>

<template>
  <el-dialog :model-value="visible" :title="requestId ? text.lookup : text.title" width="min(700px, calc(100vw - 32px))"
    class="cloud-diagnostic-dialog" append-to-body destroy-on-close @update:model-value="(value: boolean) => { if (!value) closeDialog(); }">
    <div class="diagnostic-content">
      <p class="diagnostic-notice"><el-icon><InfoFilled /></el-icon><span>{{ text.safety }}</span></p>
      <p v-if="errorText" class="diagnostic-feedback" role="status">{{ errorText }}</p>
      <p v-if="!permitted" role="alert">{{ messages.auth.permissionDenied }}</p>
      <p v-else-if="!secure" role="alert">{{ messages.cloudInventory.startFlow.httpsRequired }}</p>
      <el-form v-if="visible && permitted" v-show="!requestId" label-position="top" @submit.prevent="submit">
        <CloudSourcePicker ref="picker" allow-disabled :disabled="Boolean(requestId) || busy || querying || policyLoading" @change="sourceChanged" />
        <el-form-item :label="text.stage">
          <el-radio-group v-model="stage" :disabled="Boolean(requestId) || busy || querying">
            <el-radio-button value="tls">{{ text.tls }}</el-radio-button><el-radio-button value="token-runtime">{{ text.tokenRuntime }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-button :icon="Refresh" :disabled="!sourceTicket || busy || querying || Boolean(requestId)" :loading="policyLoading" @click="loadPolicy">{{ text.policy }}</el-button>
        <p v-if="policy" class="diagnostic-budget">{{ text.budget }}：{{ policy.limits.rpc }} RPC · {{ policy.limits.seconds }} s · 64 KiB</p>
        <template v-if="policy?.available && secure">
          <el-form-item v-if="stage === 'token-runtime'" :label="text.token" :error="tokenValue && !validToken ? messages.cloudInventory.startFlow.invalidToken : ''">
            <el-input v-model="tokenValue" :aria-label="text.token" type="password" maxlength="8192" autocomplete="new-password" :spellcheck="false" autocapitalize="off" autocorrect="off" />
            <p class="diagnostic-help">{{ text.tokenHint }}</p>
          </el-form-item>
          <el-checkbox v-model="confirmed" class="diagnostic-confirm">{{ text.confirm }}</el-checkbox>
        </template>
      </el-form>
      <template v-if="requestId">
        <dl class="diagnostic-request"><dt>{{ text.request }}</dt><dd>{{ requestId }}</dd></dl>
        <p v-if="!result" class="diagnostic-help">{{ text.unknown }}</p>
      </template>
      <template v-if="result">
        <CloudTaskSourceSummary :value="result" />
        <div class="diagnostic-status"><el-tag :type="phasePassed ? 'success' : 'warning'">{{ phasePassed ? text.phasePassed : result.status === 'completed' ? text.phaseFailed : text.pending }}</el-tag><span>{{ text.states[result.status] }}</span></div>
        <dl class="diagnostic-results">
          <dt>{{ text.stage }}</dt><dd>{{ result.stage === 'tls' ? text.tls : text.tokenRuntime }}</dd>
          <dt>{{ text.tlsResult }}</dt><dd>{{ text.checks[result.tlsState] }}</dd>
          <dt>{{ text.tokenResult }}</dt><dd>{{ text.checks[result.tokenState] }}</dd>
          <dt>{{ text.runtimeResult }}</dt><dd>{{ text.checks[result.runtimeState] }}</dd>
          <dt>{{ text.coreBuild }}</dt><dd>{{ result.coreBuild || '—' }}</dd>
          <dt>{{ text.apiBuild }}</dt><dd>{{ result.cloudApiBuild || '—' }}</dd>
          <dt>{{ text.usedRpc }}</dt><dd>{{ result.usedRpc }} / {{ result.limits.rpc }}</dd>
        </dl>
        <p v-if="resultReason" class="diagnostic-help">{{ resultReason }}</p>
        <p v-if="result.workerSlotHeld || result.unresolved" class="diagnostic-feedback" role="status">{{ text.protection }}</p>
      </template>
    </div>
    <template #footer>
      <div class="diagnostic-actions">
        <el-button @click="closeDialog">{{ messages.common.close }}</el-button>
        <el-button v-if="requestId" :icon="InfoFilled" @click="protectionVisible = true">{{ messages.cloudProtections.open }}</el-button>
        <el-button v-if="result && ['accepted', 'running'].includes(result.status)" :icon="CircleClose" :disabled="busy || querying || !permitted" @click="stop">{{ text.stop }}</el-button>
        <el-button v-if="canFinish" :icon="Check" :disabled="!permitted" @click="finish">{{ text.finish }}</el-button>
        <el-button v-if="requestId" type="primary" :icon="Search" :disabled="busy || !permitted" :loading="querying" @click="lookup()">{{ text.refresh }}</el-button>
        <el-button v-else type="primary" :icon="Connection" :disabled="!canSubmit" :loading="busy" @click="submit">{{ text.start }}</el-button>
      </div>
    </template>
    <CloudProtectionDialog v-if="protectionVisible" v-model:visible="protectionVisible" :request-id="requestId" />
  </el-dialog>
</template>

<style scoped>
.diagnostic-content { display: grid; gap: 14px; min-width: 0; overflow-wrap: anywhere; }
.diagnostic-notice { display: flex; gap: 8px; align-items: flex-start; margin: 0; padding: 10px 12px; border: 1px solid var(--panel-border); border-radius: 6px; background: var(--panel-soft-bg); color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.diagnostic-notice .el-icon { flex-shrink: 0; margin-top: 4px; }
.diagnostic-help, .diagnostic-budget { color: var(--text-muted); font-size: 13px; line-height: 1.6; margin: 8px 0; }
.diagnostic-feedback { color: var(--el-color-warning); font-size: 13px; line-height: 1.6; margin: 0; }
.diagnostic-request, .diagnostic-results { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 8px 12px; margin: 0; font-size: 13px; line-height: 1.6; }
.diagnostic-request dt, .diagnostic-results dt { color: var(--text-muted); }
.diagnostic-request dd, .diagnostic-results dd { margin: 0; min-width: 0; font-variant-numeric: tabular-nums; }
.diagnostic-status, .diagnostic-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.diagnostic-actions { justify-content: flex-end; }
.diagnostic-actions .el-button + .el-button { margin-left: 0; }
.diagnostic-confirm { height: auto; align-items: flex-start; margin-top: 12px; }
.diagnostic-confirm :deep(.el-checkbox__input) { margin-top: 4px; }
.diagnostic-confirm :deep(.el-checkbox__label) { white-space: normal; line-height: 1.6; }
@media (max-width: 480px) { .diagnostic-request, .diagnostic-results { grid-template-columns: minmax(0, 1fr); gap: 4px; } }
</style>
