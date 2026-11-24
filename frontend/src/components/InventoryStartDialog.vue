<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { CircleCheck, InfoFilled, Refresh, Search } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchInventoryPolicy, findInventoryRequest, inventoryRequestId, InventoryRequestError,
  prepareInventoryJob, startInventoryJob, type InventoryPolicy, type InventorySummary } from "../api/cloudInventory";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { formatFileSize } from "../utils/displayFormat";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";
import CloudSourcePicker from "./CloudSourcePicker.vue";
import { fetchBoundInventoryPolicy, prepareBoundInventory, startBoundInventory, fetchBoundInventory,
  findBoundInventoryRequest, type BoundInventoryPolicy, type BoundInventoryReceipt, type BoundInventorySummary } from "../api/cloudBoundInventory";
import type { CloudSourceTicket } from "../utils/cloudSourceDraft";

const props = defineProps<{ visible: boolean; requestId: string; persistRequestId: (id: string) => Promise<void> }>();
const emit = defineEmits<{ "update:visible": [value: boolean]; observed: [summary: InventorySummary] }>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudInventory.startFlow);
const policy = ref<InventoryPolicy | null>(null);
const multiMode = ref(false), boundPolicy = ref<BoundInventoryPolicy | null>(null);
const sourceTicket = ref<CloudSourceTicket | null>(null);
const sourcePicker = ref<{ isCurrent: (ticket: CloudSourceTicket) => boolean } | null>(null);
const policyLoading = ref(false);
const selectedPath = ref("");
const tokenValue = ref("");
const confirmed = ref(false);
const phase = ref<"idle" | "preparing" | "starting" | "unknown">("idle");
const requestId = ref(inventoryRequestId(props.requestId));
const querying = ref(false);
const errorText = ref("");
const queriedJob = ref<InventorySummary | null>(null);
const missingRequest = ref(false);
const ownerSession = getSessionGeneration();
let active = true;
let flowSequence = 0;
let policySequence = 0;
let handoffToken = "";
const secure = computed(() => typeof location !== "undefined" && location.protocol === "https:");
const busy = computed(() => phase.value === "preparing" || phase.value === "starting");
const pathValue = computed(() => selectedPath.value.replace(/\/+$/, ""));
const validToken = computed(() => /^[\x21-\x7e]{1,8192}$/.test(tokenValue.value));
const validPath = computed(() => {
  const path = pathValue.value, root = policy.value?.approvedRoot;
  return Boolean(root && (path === root || path.startsWith(root + "/")) && path.startsWith("/")
    && new TextEncoder().encode(path).length <= 4096 && !/[\\\x00-\x1f\x7f-\x9f]/.test(path)
    && !/%(?:2f|5c|2e|00|25)/i.test(path)
    && !Array.from(path).some(char => { const code = char.codePointAt(0)!; return code >= 0xd800 && code <= 0xdfff; })
    && path.slice(1).split("/").every(part => part && part !== "." && part !== ".."));
});
const canSubmit = computed(() => Boolean(secure.value && policy.value?.available && !policyLoading.value && !busy.value
  && !querying.value && !requestId.value && phase.value === "idle" && validPath.value && validToken.value && confirmed.value
  && (!multiMode.value || boundPolicy.value && sourceTicket.value && sourcePicker.value?.isCurrent(sourceTicket.value))));
const disabledReason = computed(() => !secure.value ? text.value.httpsRequired : policy.value?.reason === "trust_unavailable"
  ? text.value.trustUnavailable : policy.value?.reason === "directory_execution_held" ? text.value.directoryBusy : !policy.value?.available ? text.value.disabled : "");

function current(token: number) {
  if (active && flowSequence === token && getSessionGeneration() !== ownerSession) expireSession();
  return active && props.visible && flowSequence === token && getSessionGeneration() === ownerSession;
}

function purgeSensitive() {
  tokenValue.value = "";
  handoffToken = "";
  confirmed.value = false;
  flowSequence += 1;
  policySequence += 1;
  policyLoading.value = false;
  querying.value = false;
  if (busy.value || requestId.value) phase.value = "unknown";
  if (multiMode.value) { boundPolicy.value = null; sourceTicket.value = null; policy.value = null; }
}

function sourceChanged(ticket: CloudSourceTicket | null) {
  purgeSensitive(); sourceTicket.value = ticket; selectedPath.value = ticket?.source.selectedPath ?? "";
}
function sameSource(value: { connectionId: string; directoryProfileId: string; selectedPath: string } | null | undefined, ticket: CloudSourceTicket) {
  return value?.connectionId === ticket.source.connectionId && value?.directoryProfileId === ticket.source.directoryProfileId
    && value?.selectedPath === ticket.source.selectedPath;
}
function fixedLimits(limits: BoundInventoryPolicy["limits"] | undefined) {
  return limits?.rpc === 4 && limits.seconds === 30 && limits.entries === 100 && limits.bytes === 1048576 && limits.directories === 1 && limits.depth === 0;
}
function validBoundReceipt(value: BoundInventoryReceipt, ticket: CloudSourceTicket, id: string) {
  return inventoryRequestId(value.id) && inventoryRequestId(value.contextId) && value.id === value.jobId
    && value.id === value.resourceId && value.requestId === id && value.resourceKind === "inventory"
    && value.bindingState === "bound" && value.status === "queued" && value.revision === 0 && value.replayed === false
    && value.networkAccess === false && value.executionAvailable === false && value.writeAvailable === false
    && sameSource(value.source, ticket) && fixedLimits(value.limits);
}

function closeDialog() {
  purgeSensitive();
  emit("update:visible", false);
}

function sessionValid() {
  if (getSessionGeneration() === ownerSession) return true;
  purgeSensitive();
  errorText.value = text.value.sessionChanged;
  return false;
}

async function releaseRequest() {
  const previous = requestId.value;
  requestId.value = "";
  try { await props.persistRequestId(""); }
  catch (error) { requestId.value = previous; throw error; }
}

async function loadPolicy() {
  if (!props.visible || busy.value || querying.value || !sessionValid()) return;
  tokenValue.value = ""; confirmed.value = false;
  const token = ++policySequence;
  const selected = sourceTicket.value;
  if (multiMode.value && (!selected || !sourcePicker.value?.isCurrent(selected))) {
    policy.value = null; boundPolicy.value = null; errorText.value = messages.value.cloudSourcePicker.missingSource; return;
  }
  policyLoading.value = true; errorText.value = "";
  try {
    let result: InventoryPolicy;
    if (multiMode.value && selected) {
      const value = await fetchBoundInventoryPolicy(selected.source);
      if (!active || !props.visible || token !== policySequence || !sessionValid() || !sourcePicker.value?.isCurrent(selected)) return;
      if (!sameSource(value, selected) || value.networkAccess !== false || !fixedLimits(value.limits)
        || !Object.keys(selected.revisions).every(key => value.revisions?.[key as keyof typeof selected.revisions] === selected.revisions[key as keyof typeof selected.revisions])) {
        policy.value = null; boundPolicy.value = null; errorText.value = text.value.policyFailed; return;
      }
      boundPolicy.value = value;
      result = { approvedRoot: selected.source.selectedPath, available: value.available, reason: value.reasonCode,
        policyHash: value.policyHash, limits: value.limits, networkAccess: false, writeAvailable: false };
    } else result = await fetchInventoryPolicy();
    if (!active || !props.visible || token !== policySequence || !sessionValid()) return;
    const limits = result.limits;
    if (result.available && (!/^[a-f0-9]{64}$/.test(result.policyHash) || !result.approvedRoot?.startsWith("/")
      || limits?.rpc !== 4 || limits?.seconds !== 30 || limits?.entries !== 100 || limits?.bytes !== 1048576
      || limits?.directories !== 1 || limits?.depth !== 0 || result.networkAccess !== false || result.writeAvailable !== false)) {
      policy.value = null; errorText.value = text.value.policyFailed; return;
    }
    policy.value = result;
    selectedPath.value = result.approvedRoot;
  } catch (error) {
    if (active && props.visible && token === policySequence) {
      policy.value = null;
      boundPolicy.value = null;
      if (error instanceof InventoryRequestError && error.reasonCode === "multi_connection_required" && sessionValid()) {
        multiMode.value = true; errorText.value = ""; return;
      }
      errorText.value = isSessionChangedError(error) ? text.value.sessionChanged : text.value.policyFailed;
    }
  } finally { if (token === policySequence) policyLoading.value = false; }
}

async function submit() {
  if (!sessionValid() || !canSubmit.value || !policy.value) return;
  const token = ++flowSequence;
  const selected = pathValue.value, hash = policy.value.policyHash;
  const bound = multiMode.value ? sourceTicket.value : null;
  handoffToken = tokenValue.value; tokenValue.value = "";
  confirmed.value = false; errorText.value = ""; queriedJob.value = null; missingRequest.value = false;
  phase.value = "preparing";
  try {
    requestId.value = inventoryRequestId(crypto.randomUUID());
    if (!requestId.value) throw new Error("request_id_unavailable");
    // 编号先可靠留在URL；即使受理响应丢失，也只核对原意图，不重新生成任务。
    await props.persistRequestId(requestId.value);
    if (!current(token)) return;
    if (bound) {
      const prepared = await prepareBoundInventory({ ...bound.source, revisions: bound.revisions, requestId: requestId.value, policyHash: hash, confirmRead: true });
      if (!current(token) || !sourcePicker.value?.isCurrent(bound)) return;
      if (!validBoundReceipt(prepared, bound, requestId.value)) { phase.value = "unknown"; errorText.value = text.value.invalidPrepared; return; }
      phase.value = "starting";
      const pending = startBoundInventory(prepared.id, prepared.revision, hash, handoffToken);
      handoffToken = "";
      const accepted = await pending;
      if (!current(token)) return;
      if (accepted.id !== prepared.id || accepted.contextId !== prepared.contextId || accepted.requestId !== requestId.value
        || accepted.operationState !== "accepted" || accepted.retryAutomatically !== false) throw new Error("invalid_bound_acceptance");
      const result = await fetchBoundInventory(prepared.id);
      if (!current(token)) return;
      if (result.id !== prepared.id || result.contextId !== prepared.contextId || result.requestId !== requestId.value
        || result.bindingState !== "bound" || !sameSource(result.source, bound)
        || !["running", "completed", "cancelled", "interrupted", "paused"].includes(result.status)) throw new Error("invalid_bound_summary");
      await releaseRequest(); if (!current(token)) return;
      phase.value = "idle"; emit("observed", result); ElMessage.info(text.value.accepted); closeDialog(); return;
    }
    const prepared = await prepareInventoryJob({ requestId: requestId.value, selectedPath: selected, policyHash: hash, confirmRead: true });
    if (!current(token)) return;
    if (!inventoryRequestId(prepared.id) || prepared.status !== "queued" || prepared.workerSlotHeld !== false
      || prepared.revision !== 0 || prepared.budget?.status !== "active" || prepared.budget?.expired !== false
      || prepared.budget?.maxRpc !== 4 || prepared.budget?.usedRpc !== 0 || prepared.budget?.remainingRpc !== 4
      || !Number.isFinite(prepared.budget?.expiresAt)
      || prepared.mode !== "overview" || prepared.selectedPaths?.length !== 1 || prepared.selectedPaths[0] !== selected
      || prepared.approvedRoot !== selected || prepared.excludedPaths?.length !== 0 || prepared.budget?.expired
      || prepared.budget?.expiresAt <= Date.now() / 1000 || prepared.limits?.entries !== 100
      || prepared.limits?.bytes !== 1048576 || prepared.limits?.directories !== 1 || prepared.limits?.depth !== 0) {
      phase.value = "unknown"; errorText.value = text.value.invalidPrepared; return;
    }
    phase.value = "starting";
    const pending = startInventoryJob(prepared.id, prepared.revision, hash, handoffToken);
    handoffToken = "";
    const result = await pending;
    if (!current(token)) return;
    if (result.id !== prepared.id || !["running", "completed", "cancelled", "interrupted", "paused"].includes(result.status)) throw new Error("invalid_start_result");
    await releaseRequest();
    if (!current(token)) return;
    phase.value = "idle";
    emit("observed", result);
    ElMessage.info(text.value.accepted);
    closeDialog();
  } catch (error) {
    if (active && flowSequence === token) {
      phase.value = requestId.value ? "unknown" : "idle";
      errorText.value = isSessionChangedError(error) || getSessionGeneration() !== ownerSession ? text.value.sessionChanged : text.value.unknown;
    }
  } finally { handoffToken = ""; }
}

async function lookup() {
  if (!requestId.value || busy.value || querying.value || !sessionValid()) return;
  const token = ++flowSequence;
  querying.value = true; errorText.value = ""; missingRequest.value = false; tokenValue.value = "";
  try {
    const result = multiMode.value ? await findBoundInventoryRequest(requestId.value) : await findInventoryRequest(requestId.value);
    if (!current(token)) return;
    if (multiMode.value && (result as BoundInventorySummary).requestId !== requestId.value) throw new Error("invalid_original_request");
    queriedJob.value = result;
    if (result.status === "queued") { phase.value = "unknown"; errorText.value = text.value.queued; return; }
    await releaseRequest();
    if (!current(token)) return;
    requestId.value = ""; phase.value = "idle";
    emit("observed", result); ElMessage.info(text.value.resolved); closeDialog();
  } catch (error) {
    if (current(token)) {
      if (isSessionChangedError(error)) { expireSession(); return; }
      missingRequest.value = error instanceof InventoryRequestError && error.status === 404;
      errorText.value = missingRequest.value ? text.value.notFound : text.value.lookupFailed;
    }
  } finally { if (token === flowSequence) querying.value = false; }
}

function viewQueriedJob() {
  if (!queriedJob.value || !sessionValid()) return;
  emit("observed", queriedJob.value); closeDialog();
}

async function acknowledgeMissing() {
  if (!missingRequest.value || querying.value || busy.value || !sessionValid()) return;
  const id = requestId.value, token = flowSequence;
  try {
    await ElMessageBox.confirm(text.value.acknowledgeWarning, text.value.acknowledge, { type: "warning",
      confirmButtonText: messages.value.common.confirm, cancelButtonText: messages.value.common.cancel });
    if (!current(token) || id !== requestId.value) return;
    await releaseRequest();
    if (!current(token)) return;
    requestId.value = ""; phase.value = "idle"; missingRequest.value = false; errorText.value = "";
  } catch (error) {
    if (current(token) && error !== "cancel" && error !== "close") errorText.value = text.value.lookupFailed;
  }
}

function leaveSensitivePage() { if (typeof document === "undefined" || document.hidden) purgeSensitive(); }
function expireSession() { purgeSensitive(); errorText.value = text.value.sessionChanged; }
watch(() => props.requestId, value => {
  const id = inventoryRequestId(value);
  if (id === requestId.value) return;
  purgeSensitive(); requestId.value = id; phase.value = id ? "unknown" : "idle";
}, { flush: "sync" });
watch(() => props.visible, value => {
  if (value) { phase.value = requestId.value ? "unknown" : "idle"; queriedJob.value = null; void loadPolicy(); }
  else purgeSensitive();
}, { immediate: true, flush: "sync" });
onMounted(() => {
  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", purgeSensitive);
    window.addEventListener("mediaai:auth-expired", expireSession);
  }
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", leaveSensitivePage);
});
onBeforeUnmount(() => {
  active = false; purgeSensitive();
  if (typeof window !== "undefined") {
    window.removeEventListener("pagehide", purgeSensitive);
    window.removeEventListener("mediaai:auth-expired", expireSession);
  }
  if (typeof document !== "undefined") document.removeEventListener("visibilitychange", leaveSensitivePage);
});
</script>

<template>
  <el-dialog :model-value="visible" :title="requestId && !busy ? text.lookup : text.title" width="min(600px, 94vw)"
    class="inventory-start-dialog" append-to-body destroy-on-close :close-on-click-modal="true"
    @update:model-value="(value: boolean) => { if (!value) closeDialog(); }">
    <div class="inventory-start-content">
      <p class="inventory-start-notice"><el-icon><InfoFilled /></el-icon><span>{{ text.safety }}</span></p>
      <p v-if="errorText" class="inventory-start-error" role="alert">{{ errorText }}</p>
      <div v-if="multiMode" v-show="!requestId || busy">
        <el-form label-position="top"><CloudSourcePicker ref="sourcePicker" :disabled="Boolean(requestId) || busy || querying" @change="sourceChanged" /></el-form>
        <el-button :icon="Refresh" :disabled="!sourceTicket || policyLoading || busy || querying || Boolean(requestId)" @click="loadPolicy">{{ messages.cloudSourcePicker.policy }}</el-button>
      </div>
      <template v-if="requestId">
        <dl class="inventory-start-request"><dt>{{ text.requestId }}</dt><dd>{{ requestId }}</dd></dl>
        <p class="inventory-start-muted" role="status">{{ busy ? text[phase as 'preparing' | 'starting'] : text.unknown }}</p>
      </template>
      <template v-else>
        <div class="inventory-start-policy">
          <span>{{ text.root }}</span><strong>{{ policy?.approvedRoot || '—' }}</strong>
          <el-tooltip :content="text.refreshPolicy" :trigger-keys="[]"><el-button text :icon="Refresh" :aria-label="text.refreshPolicy"
            :loading="policyLoading" @click="loadPolicy" /></el-tooltip>
          <span>{{ text.limits }}</span><span v-if="policy?.limits">{{ policy.limits.rpc }} RPC · {{ policy.limits.seconds }} {{ text.seconds }} · {{ policy.limits.entries }} {{ text.entries }} · {{ formatFileSize(policy.limits.bytes) }}</span>
        </div>
        <p v-if="disabledReason" class="inventory-start-muted" role="status">{{ disabledReason }}</p>
        <el-form v-if="secure && policy?.available" label-position="top" @submit.prevent="submit">
          <el-form-item v-if="!multiMode" :label="text.selectedPath" :error="selectedPath && !validPath ? text.invalidPath : ''">
            <el-input v-model="selectedPath" :aria-label="text.selectedPath" maxlength="4096" autocomplete="off" />
          </el-form-item>
          <el-form-item :label="text.token" :error="tokenValue && !validToken ? text.invalidToken : ''">
            <el-input v-model="tokenValue" :aria-label="text.token" type="password" maxlength="8192" autocomplete="new-password"
              :spellcheck="false" autocapitalize="off" autocorrect="off" />
            <p class="inventory-start-muted inventory-start-token-hint">{{ text.tokenHint }}</p>
          </el-form-item>
          <el-checkbox v-model="confirmed" class="inventory-start-confirm">{{ text.confirm }}</el-checkbox>
        </el-form>
      </template>
    </div>
    <template #footer>
      <div class="inventory-start-footer">
        <el-button v-if="queriedJob" :icon="Search" :disabled="querying || busy" @click="viewQueriedJob">{{ text.viewTask }}</el-button>
        <el-button v-if="missingRequest" :disabled="querying || busy" @click="acknowledgeMissing">{{ text.acknowledge }}</el-button>
        <el-button @click="closeDialog">{{ messages.common.close }}</el-button>
        <el-button v-if="requestId" type="primary" :icon="Search" :loading="querying" :disabled="busy" @click="lookup">{{ text.lookup }}</el-button>
        <el-button v-else type="primary" :icon="CircleCheck" :disabled="!canSubmit" @click="submit">{{ text.start }}</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.inventory-start-content { max-height: min(58vh, 520px); overflow-y: auto; overflow-x: hidden; overflow-wrap: anywhere; padding: 0 4px 4px 0; }
.inventory-start-notice { display: flex; gap: 8px; align-items: flex-start; margin: 0 0 16px; padding: 10px 12px; border: 1px solid var(--el-border-color-light); border-radius: 6px; background: var(--el-fill-color-light); color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.inventory-start-notice .el-icon { flex-shrink: 0; margin-top: 3px; color: var(--el-color-primary); }
.inventory-start-muted { font-size: 13px; color: var(--text-muted); line-height: 1.6; }
.inventory-start-error { color: var(--el-color-danger); font-size: 13px; line-height: 1.6; margin: 0 0 12px; }
.inventory-start-request { display: grid; gap: 8px; margin: 0; }
.inventory-start-request dt { font-size: 13px; color: var(--text-muted); }
.inventory-start-request dd { margin: 0; font-variant-numeric: tabular-nums; }
.inventory-start-policy { display: grid; grid-template-columns: 110px minmax(0, 1fr) 32px; gap: 8px; align-items: center; font-size: 13px; margin-bottom: 16px; }
.inventory-start-policy > span:nth-last-child(1) { grid-column: 2 / -1; }
.inventory-start-policy > span { color: var(--text-muted); }
.inventory-start-policy strong { font-weight: 500; overflow-wrap: anywhere; }
.inventory-start-token-hint { margin: 6px 0 0; }
.inventory-start-confirm { height: auto; align-items: flex-start; white-space: normal; }
.inventory-start-confirm :deep(.el-checkbox__input) { margin-top: 4px; }
.inventory-start-confirm :deep(.el-checkbox__label) { white-space: normal; line-height: 1.6; }
.inventory-start-footer { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.inventory-start-footer .el-button + .el-button { margin-left: 0; }
@media (max-width: 480px) { .inventory-start-policy { grid-template-columns: minmax(0, 1fr) 32px; } .inventory-start-policy > span:first-child { grid-column: 1 / -1; } .inventory-start-policy > span:nth-last-child(2) { grid-column: 1 / -1; } .inventory-start-policy > span:nth-last-child(1) { grid-column: 1 / -1; } }
</style>
