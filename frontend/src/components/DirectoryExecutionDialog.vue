<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { CircleCheck, CircleClose, Document, InfoFilled, Refresh, Search } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";
import { fetchDirectoryPolicy, startDirectoryExecution, findDirectoryExecution, cancelDirectoryExecution,
  commitDirectoryLocally, type DirectoryPlan, type DirectoryExecution, type DirectoryExecutionPolicy } from "../api/cloudDirectory";
import { inventoryRequestId, InventoryRequestError } from "../api/cloudInventory";
import { useAuthStore } from "../stores/auth";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { formatFileSize } from "../utils/displayFormat";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";
import OperationLogDrawer from "./OperationLogDrawer.vue";
import DirectoryRecoveryDialog from "./DirectoryRecoveryDialog.vue";

const props = defineProps<{ visible: boolean; plan: DirectoryPlan; requestId: string;
  persistRequestId: (id: string) => Promise<void>; recoveryRequestId?: string; persistRecoveryRequestId?: (id: string) => Promise<void>;
  allowReverse?: boolean }>();
const emit = defineEmits<{ "update:visible": [value: boolean]; accepted: []; reverse: [planId: string, executionRevision: number] }>();
const text = messages.cloudInventory.directoryExecution;
const auth = useAuthStore();
const policy = ref<DirectoryExecutionPolicy | null>(null);
const execution = ref<DirectoryExecution | null>(null);
const requestId = ref(inventoryRequestId(props.requestId));
const tokenValue = ref("");
const maintenance = ref(false);
const globalFence = ref(false);
const busy = ref(false);
const policyLoading = ref(false);
const errorText = ref("");
const logsVisible = ref(false);
const recoveryVisible = ref(false);
const ownerSession = getSessionGeneration();
const secure = computed(() => typeof location !== "undefined" && location.protocol === "https:");
const canExecute = computed(() => auth.hasPermission("cloud-directory:execute") && auth.hasPermission("cloud-directory:plan"));
const canViewLogs = computed(() => Boolean(execution.value && auth.hasPermission("audit:read")
  && (execution.value.logAvailable || execution.value.pollingRecommended)));
const canStart = computed(() => secure.value && canExecute.value && props.plan.valid && policy.value?.available
  && policy.value.policyHash === props.plan.policyHash && !busy.value && !policyLoading.value && !requestId.value
  && maintenance.value && globalFence.value && /^[\x21-\x7e]{1,8192}$/.test(tokenValue.value));
const disabledReason = computed(() => !secure.value ? text.https : !canExecute.value ? text.permission
  : !props.plan.valid || (policy.value?.available && policy.value.policyHash !== props.plan.policyHash) ? text.stale
  : policy.value?.reason === "directory_execution_held" ? text.held : !policy.value?.available ? text.disabled : "");
let active = true, sequence = 0, policySequence = 0, handoffToken = "";
let timer: ReturnType<typeof setTimeout> | undefined;

function stopPolling() { if (timer !== undefined) clearTimeout(timer); timer = undefined; }
function purge() {
  tokenValue.value = ""; handoffToken = ""; maintenance.value = false; globalFence.value = false;
  sequence += 1; policySequence += 1; busy.value = false; policyLoading.value = false; stopPolling();
}
function sessionValid() {
  if (getSessionGeneration() === ownerSession) return true;
  purge(); execution.value = null; errorText.value = text.sessionChanged; return false;
}
function current(id: number) { return active && props.visible && id === sequence && sessionValid(); }
function fail(error: unknown, id: number) {
  if (!active || !props.visible || id !== sequence) return;
  if (isSessionChangedError(error) || !sessionValid()) { purge(); execution.value = null; errorText.value = text.sessionChanged; return; }
  errorText.value = error instanceof InventoryRequestError && error.status === 404 ? text.missing : text.failed;
  stopPolling();
}

function validExecution(value: DirectoryExecution) {
  const bounded = (n: number, max: number) => Number.isSafeInteger(n) && n >= 0 && n <= max;
  return value && value.planId === props.plan.id && value.requestId === requestId.value
    && ["accepted", "running", "review", "cancelled", "completed"].includes(value.status)
    && bounded(value.revision, Number.MAX_SAFE_INTEGER) && typeof value.globalSlotHeld === "boolean"
    && typeof value.workerSlotHeld === "boolean" && typeof value.pollingRecommended === "boolean"
    && typeof value.cancelRequested === "boolean" && (value.reason === null || typeof value.reason === "string")
    && Number.isSafeInteger(value.logId) && value.logId > 0 && typeof value.logAvailable === "boolean"
    && value.maxRpc === 3 + 7 * props.plan.items.length && bounded(value.usedRpc, value.maxRpc)
    && value.maxBytes === 1048576 && bounded(value.receivedBytes, Number.MAX_SAFE_INTEGER)
    && Number.isFinite(value.createdAt) && Number.isFinite(value.expiresAt) && value.expiresAt > value.createdAt
    && Array.isArray(value.items) && value.items.length === props.plan.items.length
    && value.items.every((item, index) => item.ordinal === index + 1 && item.sourceName === props.plan.items[index]?.sourceName
      && item.targetName === props.plan.items[index]?.targetName && Object.prototype.hasOwnProperty.call(text.itemStates, item.state))
    && Array.isArray(value.events) && value.events.length <= value.maxRpc && value.events.length === value.usedRpc
    && value.events.every((event, index) => event.ordinal === index + 1
      && ["GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath", "RenameFile"].includes(event.method)
      && Object.prototype.hasOwnProperty.call(text.phases, event.phase) && bounded(event.receivedBytes, Number.MAX_SAFE_INTEGER)
      && (event.responseCode === null || /^[a-z_]{1,40}$/.test(event.responseCode))
      && (event.itemOrdinal === null || (Number.isInteger(event.itemOrdinal) && event.itemOrdinal >= 1 && event.itemOrdinal <= value.items.length)))
    && value.networkAccess === false && value.replayed === false;
}

function accept(value: DirectoryExecution) {
  if (!validExecution(value)) { execution.value = null; errorText.value = text.invalid; stopPolling(); return; }
  if (execution.value && value.revision < execution.value.revision) { errorText.value = text.invalid; stopPolling(); return; }
  execution.value = value;
  emit("accepted");
  schedule();
}
function schedule() {
  stopPolling();
  if (active && props.visible && execution.value?.pollingRecommended && (typeof document === "undefined" || !document.hidden)) {
    timer = setTimeout(() => { timer = undefined; void lookup(); }, 1500);
  }
}

async function loadPolicy() {
  if (!props.visible || busy.value || !sessionValid()) return;
  tokenValue.value = ""; maintenance.value = false; globalFence.value = false;
  const id = ++policySequence;
  policyLoading.value = true; errorText.value = "";
  try {
    const value = await fetchDirectoryPolicy();
    if (!active || !props.visible || id !== policySequence || !sessionValid()) return;
    if (typeof value.available !== "boolean" || value.networkAccess !== false || value.controlledMode !== true
      || value.atomicIdentityGuaranteed !== false || value.oldPlansGloballyInvalidated !== true || value.automaticRescan !== false
      || value.limits?.items !== 5 || value.limits?.baseRpc !== 3 || value.limits?.rpcPerItem !== 7
      || value.limits?.seconds !== 180 || value.limits?.bytes !== 1048576
      || (value.available && !/^[a-f0-9]{64}$/.test(value.policyHash))) {
      policy.value = null; errorText.value = text.policyFailed; return;
    }
    policy.value = value;
  } catch (error) {
    if (active && props.visible && id === policySequence) {
      policy.value = null; errorText.value = isSessionChangedError(error) ? text.sessionChanged : text.policyFailed;
    }
  } finally { if (id === policySequence) policyLoading.value = false; }
}

async function start() {
  if (!sessionValid() || !canStart.value || !policy.value) return;
  const id = ++sequence;
  const planId = props.plan.id, revision = props.plan.revision, hash = policy.value.policyHash;
  busy.value = true; errorText.value = ""; handoffToken = tokenValue.value; tokenValue.value = "";
  maintenance.value = false; globalFence.value = false;
  try {
    requestId.value = inventoryRequestId(crypto.randomUUID());
    if (!requestId.value) throw new Error("request_id_unavailable");
    // 先保存独立执行编号；POST结果不明时只能查询，不重新交接令牌。
    await props.persistRequestId(requestId.value);
    if (!current(id)) return;
    const pending = startDirectoryExecution(planId, { requestId: requestId.value, expectedRevision: revision,
      policyHash: hash, confirmMaintenance: true, confirmGlobalFence: true, apiToken: handoffToken });
    handoffToken = "";
    emit("accepted");
    const result = await pending;
    if (current(id)) accept(result);
  } catch (error) { fail(error, id); }
  finally { if (id === sequence) { handoffToken = ""; busy.value = false; } }
}

async function lookup() {
  if (!requestId.value || busy.value || !sessionValid()) return;
  const id = ++sequence;
  stopPolling(); tokenValue.value = ""; busy.value = true; errorText.value = "";
  try { const result = await findDirectoryExecution(requestId.value); if (current(id)) accept(result); }
  catch (error) { fail(error, id); }
  finally { if (id === sequence) busy.value = false; }
}

async function act(kind: "cancel" | "commit") {
  if (busy.value || !execution.value || !sessionValid()) return;
  const value = execution.value;
  if (kind === "commit" && (!canExecute.value || value.reason !== "awaiting_path_sync" || value.workerSlotHeld)) return;
  if (kind === "cancel" && (value.cancelRequested || !value.globalSlotHeld)) return;
  const id = ++sequence;
  stopPolling(); busy.value = true; errorText.value = "";
  try {
    const result = await (kind === "cancel" ? cancelDirectoryExecution(value.planId, value.revision)
      : commitDirectoryLocally(value.planId, value.revision));
    if (current(id)) accept(result);
  } catch (error) { fail(error, id); }
  finally { if (id === sequence) busy.value = false; }
}

async function acknowledge() {
  if (busy.value || !execution.value || execution.value.globalSlotHeld || execution.value.workerSlotHeld || !sessionValid()) return;
  const id = ++sequence;
  busy.value = true; stopPolling();
  try {
    await ElMessageBox.confirm(text.acknowledgeHint, text.acknowledge, { type: "info",
      confirmButtonText: messages.common.confirm, cancelButtonText: messages.common.cancel });
    if (!current(id)) return;
    const previous = requestId.value;
    if (props.persistRecoveryRequestId) await props.persistRecoveryRequestId("");
    if (!current(id)) return;
    requestId.value = "";
    try { await props.persistRequestId(""); }
    catch (error) { if (current(id)) requestId.value = previous; throw error; }
    if (!current(id)) return;
    requestId.value = ""; close();
  } catch (error) { if (error !== "cancel" && error !== "close") fail(error, id); }
  finally { if (id === sequence) busy.value = false; }
}

function close() { recoveryVisible.value = false; logsVisible.value = false; purge(); execution.value = null; emit("update:visible", false); }
function hidden() { if (typeof document === "undefined" || document.hidden) purge(); }
function expired() { recoveryVisible.value = false; logsVisible.value = false; purge(); execution.value = null; errorText.value = text.sessionChanged; }
watch(() => props.requestId, value => { const next = inventoryRequestId(value); if (next !== requestId.value) { purge(); requestId.value = next; execution.value = null; } }, { flush: "sync" });
watch(() => props.plan.id, () => { purge(); execution.value = null; });
watch(canExecute, value => { if (!value) purge(); }, { flush: "sync" });
watch(() => props.visible, value => {
  purge(); errorText.value = ""; execution.value = null;
  logsVisible.value = false;
  recoveryVisible.value = false;
  if (value) { requestId.value = inventoryRequestId(props.requestId); void loadPolicy(); }
}, { immediate: true, flush: "sync" });
onMounted(() => {
  if (typeof window !== "undefined") { window.addEventListener("pagehide", purge); window.addEventListener("mediaai:auth-expired", expired); }
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", hidden);
});
onBeforeUnmount(() => {
  active = false; purge();
  if (typeof window !== "undefined") { window.removeEventListener("pagehide", purge); window.removeEventListener("mediaai:auth-expired", expired); }
  if (typeof document !== "undefined") document.removeEventListener("visibilitychange", hidden);
});
</script>

<template>
  <el-dialog :model-value="visible" :title="text.title" width="min(820px, 94vw)" append-to-body destroy-on-close
    @update:model-value="(value: boolean) => { if (!value) close(); }">
    <div class="directory-execution-content">
      <p class="directory-execution-notice"><el-icon><InfoFilled /></el-icon><span>{{ text.scope }}</span></p>
      <p v-if="errorText" class="directory-execution-error" role="alert">{{ errorText }}</p>
      <dl class="directory-execution-facts"><dt>{{ text.parent }}</dt><dd>{{ plan.parentPath }}</dd>
        <template v-if="requestId"><dt>{{ text.requestId }}</dt><dd><code>{{ requestId }}</code></dd></template>
      </dl>
      <template v-if="!requestId">
        <p class="directory-execution-muted">{{ formatMessage(text.limits, { rpc: 3 + 7 * plan.items.length }) }}</p>
        <p v-if="disabledReason" class="directory-execution-muted" role="status">{{ disabledReason }}</p>
        <el-table :data="plan.items" border class="data-table" max-height="210">
          <el-table-column prop="sourceName" :label="messages.cloudInventory.directoryPlan.source" min-width="200" show-overflow-tooltip />
          <el-table-column prop="targetName" :label="messages.cloudInventory.directoryPlan.target" min-width="200" show-overflow-tooltip />
        </el-table>
        <el-form v-if="secure && canExecute && policy?.available && plan.valid" label-position="top" @submit.prevent="start">
          <el-form-item :label="text.token">
            <el-input v-model="tokenValue" :aria-label="text.token" type="password" maxlength="8192" autocomplete="new-password"
              autocapitalize="off" autocorrect="off" :spellcheck="false" />
            <p class="directory-execution-muted">{{ text.tokenHint }}</p>
          </el-form-item>
          <el-checkbox v-model="maintenance" class="directory-execution-check">{{ text.maintenance }}</el-checkbox>
          <el-checkbox v-model="globalFence" class="directory-execution-check">{{ text.globalFence }}</el-checkbox>
        </el-form>
      </template>
      <p v-else-if="!execution" class="directory-execution-muted" role="status">{{ text.failed }}</p>
      <template v-if="execution">
        <div class="directory-execution-state"><el-tag :type="execution.status === 'completed' || execution.reason === 'positions_reconciled' ? 'success' : execution.globalSlotHeld ? 'warning' : 'info'">{{ execution.reason === 'positions_reconciled' ? text.reconciled : text.status[execution.status] }}</el-tag>
          <span>{{ formatMessage(text.used, { used: execution.usedRpc, max: execution.maxRpc, bytes: formatFileSize(execution.receivedBytes) }) }}</span></div>
        <p v-if="execution.reason && text.reasons[execution.reason]" class="directory-execution-muted">{{ text.reasons[execution.reason] }}</p>
        <p v-if="execution.status === 'completed'" class="directory-execution-muted">{{ text.completed }}</p>
        <p v-else-if="execution.reason === 'positions_reconciled'" class="directory-execution-muted">{{ text.reconciledHint }}</p>
        <p v-else-if="execution.reason === 'awaiting_path_sync'" class="directory-execution-muted">{{ text.localHint }}</p>
        <p v-else-if="execution.globalSlotHeld && !execution.pollingRecommended" class="directory-execution-muted" role="status">{{ text.frozen }}</p>
        <el-table :data="execution.items" border class="data-table" max-height="250">
          <el-table-column prop="sourceName" :label="messages.cloudInventory.directoryPlan.source" min-width="190" show-overflow-tooltip />
          <el-table-column prop="targetName" :label="messages.cloudInventory.directoryPlan.target" min-width="190" show-overflow-tooltip />
          <el-table-column :label="text.itemState" width="126" fixed="right"><template #default="{ row }"><span>{{ text.itemStates[row.state] }}</span></template></el-table-column>
        </el-table>
        <el-collapse class="directory-execution-events"><el-collapse-item :title="text.events" name="events">
          <p class="directory-execution-muted">{{ text.eventHint }}</p>
          <el-table :data="execution.events" border class="data-table" max-height="230" :empty-text="text.noEvents">
            <el-table-column prop="ordinal" label="#" width="54" />
            <el-table-column prop="method" :label="text.method" min-width="170" />
            <el-table-column :label="text.phase" width="110"><template #default="{ row }">{{ text.phases[row.phase] }}</template></el-table-column>
            <el-table-column prop="responseCode" :label="text.response" min-width="145" />
          </el-table>
        </el-collapse-item></el-collapse>
      </template>
    </div>
    <template #footer><div class="directory-execution-footer">
      <el-button v-if="allowReverse && execution && !execution.globalSlotHeld && (execution.status === 'completed' || execution.reason === 'positions_reconciled')"
        :icon="Document" :disabled="busy" @click="emit('reverse', plan.id, execution.revision)">{{ messages.cloudInventory.directoryPlan.reverse }}</el-button>
      <el-button v-if="execution && persistRecoveryRequestId && (recoveryRequestId || execution.latestRecovery || (execution.globalSlotHeld && !execution.pollingRecommended && execution.reason !== 'awaiting_path_sync'))"
        :icon="Search" :disabled="busy" @click="recoveryVisible = true">{{ execution.globalSlotHeld ? text.recovery : text.recoveryLookup }}</el-button>
      <el-tooltip v-if="execution && auth.hasPermission('audit:read')" :content="text.logsCleared" :disabled="execution.logAvailable || execution.pollingRecommended" :trigger-keys="[]"
        :enterable="false" placement="top" :show-after="250" popper-class="directory-execution-tooltip">
        <span><el-button :icon="Document" :disabled="!canViewLogs" @click="logsVisible = true">{{ text.logs }}</el-button></span>
      </el-tooltip>
      <el-button v-if="!requestId" :icon="Refresh" :loading="policyLoading" :disabled="busy" @click="loadPolicy">{{ text.refresh }}</el-button>
      <el-tooltip v-if="execution?.globalSlotHeld && (execution.pollingRecommended || execution.workerSlotHeld) && !execution.cancelRequested" :content="text.cancelledHint" :trigger-keys="[]"
        :enterable="false" placement="top" :show-after="250" popper-class="directory-execution-tooltip">
        <el-button :icon="CircleClose" :disabled="busy" @click="act('cancel')">{{ text.cancel }}</el-button>
      </el-tooltip>
      <el-button v-if="execution?.reason === 'awaiting_path_sync' && !execution.workerSlotHeld && canExecute" :icon="CircleCheck" :disabled="busy" @click="act('commit')">{{ text.commit }}</el-button>
      <el-button v-if="requestId" :icon="Search" :loading="busy" @click="lookup">{{ text.lookup }}</el-button>
      <el-button v-else type="primary" :icon="CircleCheck" :disabled="!canStart" :loading="busy" @click="start">{{ text.start }}</el-button>
      <el-button v-if="execution && !execution.globalSlotHeld && !execution.workerSlotHeld" :disabled="busy" @click="acknowledge">{{ text.acknowledge }}</el-button>
      <el-button @click="close">{{ messages.common.close }}</el-button>
    </div></template>
  </el-dialog>
  <OperationLogDrawer v-if="execution" v-model:visible="logsVisible" task-type="cloud_directory_execution"
    :task-id="execution.logId" :title="text.logs" />
  <DirectoryRecoveryDialog v-if="execution && persistRecoveryRequestId" v-model:visible="recoveryVisible" :plan="plan" :execution="execution"
    :request-id="recoveryRequestId || ''" :persist-request-id="persistRecoveryRequestId" @resolved="lookup" />
</template>

<style scoped>
.directory-execution-content { min-width: 0; max-height: min(60vh, 640px); overflow: auto; overflow-wrap: anywhere; padding-right: 4px; }
.directory-execution-notice { display: flex; gap: 8px; align-items: flex-start; margin: 0 0 16px; padding: 10px 12px; border: 1px solid var(--el-border-color-light); border-radius: 6px; background: var(--el-fill-color-light); color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.directory-execution-notice .el-icon { flex-shrink: 0; margin-top: 3px; color: var(--el-color-primary); }
.directory-execution-facts { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 8px; margin: 0 0 12px; font-size: 13px; }
.directory-execution-facts dt { color: var(--text-muted); }
.directory-execution-facts dd { margin: 0; }
.directory-execution-muted { color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.directory-execution-error { color: var(--el-color-danger); font-size: 13px; }
.directory-execution-content .el-form { margin-top: 16px; }
.directory-execution-check { display: flex; height: auto; align-items: flex-start; margin: 12px 0; white-space: normal; }
.directory-execution-check :deep(.el-checkbox__input) { margin-top: 4px; }
.directory-execution-check :deep(.el-checkbox__label) { white-space: normal; line-height: 1.6; }
.directory-execution-state, .directory-execution-footer { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.directory-execution-state { margin: 12px 0; font-size: 13px; }
.directory-execution-events { margin-top: 16px; }
.directory-execution-footer { justify-content: flex-end; }
.directory-execution-footer .el-button + .el-button { margin-left: 0; }
:global(.directory-execution-tooltip) { max-width: min(320px, calc(100vw - 24px)); white-space: normal; overflow-wrap: anywhere; pointer-events: none; }
@media (max-width: 480px) { .directory-execution-facts { grid-template-columns: minmax(0, 1fr); } }
</style>
