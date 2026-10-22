<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { CircleCheck, InfoFilled, Refresh, Search } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchInventoryPolicy, findInventoryRequest, inventoryRequestId, InventoryRequestError,
  prepareInventoryJob, startInventoryJob, type InventoryPolicy, type InventorySummary } from "../api/cloudInventory";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { formatFileSize } from "../utils/displayFormat";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";

const props = defineProps<{ visible: boolean; requestId: string; persistRequestId: (id: string) => Promise<void> }>();
const emit = defineEmits<{ "update:visible": [value: boolean]; observed: [summary: InventorySummary] }>();
const text = messages.cloudInventory.startFlow;
const policy = ref<InventoryPolicy | null>(null);
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
  && !querying.value && !requestId.value && phase.value === "idle" && validPath.value && validToken.value && confirmed.value));
const disabledReason = computed(() => !secure.value ? text.httpsRequired : policy.value?.reason === "trust_unavailable"
  ? text.trustUnavailable : policy.value?.reason === "directory_execution_held" ? text.directoryBusy : !policy.value?.available ? text.disabled : "");

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
}

function closeDialog() {
  purgeSensitive();
  emit("update:visible", false);
}

function sessionValid() {
  if (getSessionGeneration() === ownerSession) return true;
  purgeSensitive();
  errorText.value = text.sessionChanged;
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
  policyLoading.value = true; errorText.value = "";
  try {
    const result = await fetchInventoryPolicy();
    if (!active || !props.visible || token !== policySequence || !sessionValid()) return;
    const limits = result.limits;
    if (result.available && (!/^[a-f0-9]{64}$/.test(result.policyHash) || !result.approvedRoot?.startsWith("/")
      || limits?.rpc !== 4 || limits?.seconds !== 30 || limits?.entries !== 100 || limits?.bytes !== 1048576
      || limits?.directories !== 1 || limits?.depth !== 0 || result.networkAccess !== false || result.writeAvailable !== false)) {
      policy.value = null; errorText.value = text.policyFailed; return;
    }
    policy.value = result;
    selectedPath.value = result.approvedRoot;
  } catch (error) {
    if (active && props.visible && token === policySequence) {
      policy.value = null;
      errorText.value = isSessionChangedError(error) ? text.sessionChanged : text.policyFailed;
    }
  } finally { if (token === policySequence) policyLoading.value = false; }
}

async function submit() {
  if (!sessionValid() || !canSubmit.value || !policy.value) return;
  const token = ++flowSequence;
  const selected = pathValue.value, hash = policy.value.policyHash;
  handoffToken = tokenValue.value; tokenValue.value = "";
  confirmed.value = false; errorText.value = ""; queriedJob.value = null; missingRequest.value = false;
  phase.value = "preparing";
  try {
    requestId.value = inventoryRequestId(crypto.randomUUID());
    if (!requestId.value) throw new Error("request_id_unavailable");
    // 编号先可靠留在URL；即使受理响应丢失，也只核对原意图，不重新生成任务。
    await props.persistRequestId(requestId.value);
    if (!current(token)) return;
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
      phase.value = "unknown"; errorText.value = text.invalidPrepared; return;
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
    ElMessage.info(text.accepted);
    closeDialog();
  } catch (error) {
    if (active && flowSequence === token) {
      phase.value = requestId.value ? "unknown" : "idle";
      errorText.value = isSessionChangedError(error) || getSessionGeneration() !== ownerSession ? text.sessionChanged : text.unknown;
    }
  } finally { handoffToken = ""; }
}

async function lookup() {
  if (!requestId.value || busy.value || querying.value || !sessionValid()) return;
  const token = ++flowSequence;
  querying.value = true; errorText.value = ""; missingRequest.value = false; tokenValue.value = "";
  try {
    const result = await findInventoryRequest(requestId.value);
    if (!current(token)) return;
    queriedJob.value = result;
    if (result.status === "queued") { phase.value = "unknown"; errorText.value = text.queued; return; }
    await releaseRequest();
    if (!current(token)) return;
    requestId.value = ""; phase.value = "idle";
    emit("observed", result); ElMessage.info(text.resolved); closeDialog();
  } catch (error) {
    if (current(token)) {
      if (isSessionChangedError(error)) { expireSession(); return; }
      missingRequest.value = error instanceof InventoryRequestError && error.status === 404;
      errorText.value = missingRequest.value ? text.notFound : text.lookupFailed;
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
    await ElMessageBox.confirm(text.acknowledgeWarning, text.acknowledge, { type: "warning",
      confirmButtonText: messages.common.confirm, cancelButtonText: messages.common.cancel });
    if (!current(token) || id !== requestId.value) return;
    await releaseRequest();
    if (!current(token)) return;
    requestId.value = ""; phase.value = "idle"; missingRequest.value = false; errorText.value = "";
  } catch (error) {
    if (current(token) && error !== "cancel" && error !== "close") errorText.value = text.lookupFailed;
  }
}

function leaveSensitivePage() { if (typeof document === "undefined" || document.hidden) purgeSensitive(); }
function expireSession() { purgeSensitive(); errorText.value = text.sessionChanged; }
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
          <el-form-item :label="text.selectedPath" :error="selectedPath && !validPath ? text.invalidPath : ''">
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
