<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { CircleCheck, CircleClose, InfoFilled, Search, RefreshLeft } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";
import { startDirectoryRecovery, findDirectoryRecovery, cancelDirectoryRecovery, commitDirectoryRecovery, reviewDirectoryRecoveryWorker,
  type DirectoryPlan, type DirectoryExecution, type DirectoryRecovery } from "../api/cloudDirectory";
import { inventoryRequestId, InventoryRequestError } from "../api/cloudInventory";
import { useAuthStore } from "../stores/auth";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";

const props = defineProps<{ visible: boolean; plan: DirectoryPlan; execution: DirectoryExecution;
  requestId: string; persistRequestId: (id: string) => Promise<void> }>();
const emit = defineEmits<{ "update:visible": [value: boolean]; resolved: [] }>();
const text = messages.cloudInventory.directoryRecovery;
const auth = useAuthStore();
const requestId = ref("");
const recovery = ref<DirectoryRecovery | null>(null);
const tokenValue = ref("");
const maintenance = ref(false), remoteIdle = ref(false), positionsConfirmed = ref(false);
const busy = ref(false), errorText = ref("");
const reviewForm = ref(false), clientsStopped = ref(false), reviewRemoteIdle = ref(false);
const ownerSession = getSessionGeneration();
let active = true, sequence = 0, handoffToken = "";
let timer: ReturnType<typeof setTimeout> | undefined;
const secure = computed(() => typeof location !== "undefined" && location.protocol === "https:");
const permitted = computed(() => auth.hasPermission("cloud-directory:plan") && auth.hasPermission("cloud-directory:execute"));
const disabledReason = computed(() => !secure.value ? text.https : !permitted.value ? text.permission
  : !props.execution.globalSlotHeld || props.execution.pollingRecommended ? text.blocked : "");
const canStart = computed(() => !disabledReason.value && !busy.value && !requestId.value && maintenance.value && remoteIdle.value
  && /^[\x21-\x7e]{1,8192}$/.test(tokenValue.value));
const canCommit = computed(() => permitted.value && positionsConfirmed.value && !busy.value && recovery.value?.status === "ready"
  && !recovery.value.workerSlotHeld && !recovery.value.recoverySlotHeld && !recovery.value.cancelRequested);
const canPrepareNext = computed(() => props.execution.globalSlotHeld && permitted.value && !busy.value && recovery.value
  && ["review", "ready"].includes(recovery.value.status) && !recovery.value.workerSlotHeld && !recovery.value.recoverySlotHeld && recovery.value.attempt < 5);
const canReviewWorker = computed(() => permitted.value && auth.hasPermission("settings:write") && Boolean(recovery.value?.recoverySlotHeld));
const canSubmitReview = computed(() => canReviewWorker.value && reviewForm.value && clientsStopped.value && reviewRemoteIdle.value && !busy.value);

function stopPolling() { if (timer !== undefined) clearTimeout(timer); timer = undefined; }
function purge() {
  sequence += 1; tokenValue.value = ""; handoffToken = ""; maintenance.value = false; remoteIdle.value = false;
  positionsConfirmed.value = false; busy.value = false; stopPolling();
  reviewForm.value = false; clientsStopped.value = false; reviewRemoteIdle.value = false;
}
function sessionValid() {
  if (getSessionGeneration() === ownerSession) return true;
  purge(); recovery.value = null; errorText.value = text.sessionChanged; return false;
}
function current(id: number) { return active && props.visible && id === sequence && sessionValid(); }
function fail(error: unknown, id: number) {
  if (!active || !props.visible || id !== sequence) return;
  stopPolling();
  if (isSessionChangedError(error) || !sessionValid()) { purge(); recovery.value = null; errorText.value = text.sessionChanged; return; }
  errorText.value = error instanceof InventoryRequestError && error.status === 404 ? text.missing : text.failed;
}
function valid(value: DirectoryRecovery) {
  const bounded = (n: number, max: number) => Number.isSafeInteger(n) && n >= 0 && n <= max;
  return value && Boolean(inventoryRequestId(value.id)) && value.requestId === requestId.value && value.planId === props.plan.id
    && bounded(value.attempt, 5) && value.attempt >= 1 && bounded(value.revision, Number.MAX_SAFE_INTEGER)
    && ["accepted", "running", "ready", "review", "committed"].includes(value.status)
    && [value.workerSlotHeld, value.recoverySlotHeld, value.pollingRecommended, value.cancelRequested].every(flag => typeof flag === "boolean")
    && value.maxRpc === 4 + 2 * props.plan.items.length && bounded(value.usedRpc, value.maxRpc)
    && value.maxBytes === 1048576 && bounded(value.receivedBytes, Number.MAX_SAFE_INTEGER)
    && Number.isFinite(value.createdAt) && Number.isFinite(value.expiresAt) && value.expiresAt > value.createdAt && value.expiresAt - value.createdAt <= 90
    && Array.isArray(value.items) && value.items.length === props.plan.items.length
    && value.items.every((item, index) => item.ordinal === index + 1 && item.sourceName === props.plan.items[index]?.sourceName
      && item.targetName === props.plan.items[index]?.targetName && Object.prototype.hasOwnProperty.call(text.positions, item.position))
    && Array.isArray(value.events) && value.events.length === value.usedRpc && value.events.length <= 14
    && value.events.every((event, index) => event.ordinal === index + 1 && ["GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath"].includes(event.method)
      && ["sending", "responded", "unknown"].includes(event.phase) && bounded(event.receivedBytes, Number.MAX_SAFE_INTEGER)
      && (event.responseCode === null || ["ok", "not_found"].includes(event.responseCode)))
    && value.originalOutcomeRewritten === false && value.networkAccess === false && value.replayed === false;
}
function accept(value: DirectoryRecovery) {
  if (!valid(value) || (recovery.value && value.id === recovery.value.id && value.revision < recovery.value.revision)) {
    recovery.value = null; errorText.value = text.invalid; stopPolling(); return;
  }
  recovery.value = value;
  if (value.status === "committed") emit("resolved");
  stopPolling();
  if (value.pollingRecommended && (typeof document === "undefined" || !document.hidden)) timer = setTimeout(() => { timer = undefined; void lookup(); }, 1500);
}

async function start() {
  if (!sessionValid() || !canStart.value) return;
  const id = ++sequence, planId = props.plan.id, revision = props.execution.revision;
  busy.value = true; errorText.value = ""; handoffToken = tokenValue.value; tokenValue.value = "";
  maintenance.value = false; remoteIdle.value = false;
  try {
    requestId.value = inventoryRequestId(crypto.randomUUID());
    if (!requestId.value) throw new Error("request_id_unavailable");
    await props.persistRequestId(requestId.value);
    if (!current(id)) return;
    const pending = startDirectoryRecovery(planId, { requestId: requestId.value, expectedRevision: revision,
      confirmMaintenance: true, confirmRemoteIdle: true, apiToken: handoffToken });
    handoffToken = "";
    const value = await pending;
    if (current(id)) accept(value);
  } catch (error) { fail(error, id); }
  finally { if (id === sequence) { handoffToken = ""; busy.value = false; } }
}
async function lookup() {
  if (!requestId.value || busy.value || !sessionValid()) return;
  const id = ++sequence;
  busy.value = true; errorText.value = ""; tokenValue.value = ""; positionsConfirmed.value = false; stopPolling();
  try { const value = await findDirectoryRecovery(requestId.value); if (current(id)) accept(value); }
  catch (error) { fail(error, id); }
  finally { if (id === sequence) busy.value = false; }
}
async function act(kind: "cancel" | "commit") {
  if (!recovery.value || busy.value || !sessionValid() || (kind === "commit" && !canCommit.value)) return;
  if (kind === "cancel" && (!recovery.value.recoverySlotHeld || recovery.value.cancelRequested)) return;
  const previous = recovery.value, id = ++sequence;
  busy.value = true; errorText.value = ""; stopPolling(); positionsConfirmed.value = false;
  try {
    const value = await (kind === "commit" ? commitDirectoryRecovery(previous.id, previous.revision) : cancelDirectoryRecovery(previous.id, previous.revision));
    if (current(id)) accept(value);
  } catch (error) { fail(error, id); }
  finally { if (id === sequence) busy.value = false; }
}
async function prepareNext() {
  if (!sessionValid() || !canPrepareNext.value) return;
  const id = ++sequence;
  busy.value = true; stopPolling();
  try {
    await ElMessageBox.confirm(text.newBudgetHint, text.newBudget, { type: "warning",
      confirmButtonText: messages.common.confirm, cancelButtonText: messages.common.cancel });
    if (!current(id)) return;
    const previous = requestId.value;
    requestId.value = "";
    try { await props.persistRequestId(""); } catch (error) { if (current(id)) requestId.value = previous; throw error; }
    if (!current(id)) return;
    recovery.value = null; tokenValue.value = ""; maintenance.value = false; remoteIdle.value = false; positionsConfirmed.value = false;
  } catch (error) { if (error !== "cancel" && error !== "close") fail(error, id); }
  finally { if (id === sequence) busy.value = false; }
}
function openWorkerReview() {
  if (busy.value || !canReviewWorker.value || !sessionValid()) return;
  purge(); errorText.value = ""; reviewForm.value = true;
}
async function reviewWorker() {
  if (!canSubmitReview.value || !recovery.value || !sessionValid()) return;
  const previous = recovery.value, id = ++sequence;
  reviewForm.value = false; clientsStopped.value = false; reviewRemoteIdle.value = false;
  busy.value = true; errorText.value = ""; stopPolling();
  try {
    const value = await reviewDirectoryRecoveryWorker(previous.id, { expectedRevision: previous.revision,
      confirmClientsStopped: true, confirmRemoteIdle: true });
    if (current(id)) accept(value);
  } catch (error) { fail(error, id); }
  finally { if (id === sequence) busy.value = false; }
}
function close() { purge(); recovery.value = null; emit("update:visible", false); }
function hidden() { if (typeof document === "undefined" || document.hidden) purge(); }
function expired() { purge(); recovery.value = null; errorText.value = text.sessionChanged; }
watch(() => props.requestId, value => { const next = inventoryRequestId(value); if (next !== requestId.value) { purge(); requestId.value = next; recovery.value = null; } }, { flush: "sync" });
watch(permitted, value => { if (!value) purge(); }, { flush: "sync" });
watch(() => auth.hasPermission("settings:write"), value => { if (!value) purge(); }, { flush: "sync" });
watch(() => props.plan.id, () => { purge(); recovery.value = null; });
watch(() => props.visible, value => {
  purge(); recovery.value = null; errorText.value = "";
  if (value) requestId.value = inventoryRequestId(props.requestId) || inventoryRequestId(props.execution.latestRecovery?.requestId);
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
  <el-dialog :model-value="visible" :title="text.title" width="min(800px, 94vw)" append-to-body destroy-on-close
    @update:model-value="(value: boolean) => { if (!value) close(); }">
    <div class="directory-recovery-content">
      <p class="directory-recovery-notice"><el-icon><InfoFilled /></el-icon><span>{{ text.scope }}</span></p>
      <p class="directory-recovery-muted">{{ plan.parentPath }}</p>
      <p v-if="errorText" class="directory-recovery-error" role="alert">{{ errorText }}</p>
      <section v-if="reviewForm" class="directory-recovery-review">
        <p class="directory-recovery-muted">{{ text.reviewHint }}</p>
        <el-checkbox v-model="clientsStopped" class="directory-recovery-check">{{ text.clientsStopped }}</el-checkbox>
        <el-checkbox v-model="reviewRemoteIdle" class="directory-recovery-check">{{ text.reviewRemoteIdle }}</el-checkbox>
      </section>
      <p v-if="requestId" class="directory-recovery-request">{{ text.requestId }}<code>{{ requestId }}</code></p>
      <template v-else>
        <p class="directory-recovery-muted">{{ formatMessage(text.limits, { rpc: 4 + 2 * plan.items.length }) }}</p>
        <p v-if="disabledReason" class="directory-recovery-muted" role="status">{{ disabledReason }}</p>
        <el-form v-else label-position="top" @submit.prevent="start">
          <el-form-item :label="text.token"><el-input v-model="tokenValue" :aria-label="text.token" type="password" maxlength="8192"
            autocomplete="new-password" autocapitalize="off" autocorrect="off" :spellcheck="false" />
            <p class="directory-recovery-muted">{{ text.tokenHint }}</p>
          </el-form-item>
          <el-checkbox v-model="maintenance" class="directory-recovery-check">{{ text.maintenance }}</el-checkbox>
          <el-checkbox v-model="remoteIdle" class="directory-recovery-check">{{ text.remoteIdle }}</el-checkbox>
        </el-form>
      </template>
      <template v-if="recovery">
        <div class="directory-recovery-state"><el-tag :type="recovery.status === 'committed' ? 'success' : recovery.status === 'review' ? 'warning' : 'info'">{{ text.status[recovery.status] }}</el-tag>
          <span>{{ formatMessage(text.attempt, { attempt: recovery.attempt, used: recovery.usedRpc, max: recovery.maxRpc }) }}</span></div>
        <p class="directory-recovery-muted">{{ recovery.status === 'committed' ? text.committed : text.causalWarning }}</p>
        <p v-if="recovery.reason && messages.cloudInventory.directoryExecution.reasons[recovery.reason]" class="directory-recovery-muted">{{ messages.cloudInventory.directoryExecution.reasons[recovery.reason] }}</p>
        <el-table :data="recovery.items" border class="data-table" max-height="300">
          <el-table-column prop="sourceName" :label="messages.cloudInventory.directoryPlan.source" min-width="190" show-overflow-tooltip />
          <el-table-column prop="targetName" :label="messages.cloudInventory.directoryPlan.target" min-width="190" show-overflow-tooltip />
          <el-table-column :label="text.position" width="150" fixed="right"><template #default="{ row }">{{ text.positions[row.position] }}</template></el-table-column>
        </el-table>
        <el-checkbox v-if="recovery.status === 'ready' && !recovery.workerSlotHeld && !recovery.recoverySlotHeld" v-model="positionsConfirmed" class="directory-recovery-check">{{ text.positionConfirm }}</el-checkbox>
        <p v-else-if="recovery.status === 'review'" class="directory-recovery-muted">{{ text.frozen }}</p>
      </template>
    </div>
    <template #footer><div class="directory-recovery-footer">
      <template v-if="reviewForm">
        <el-button @click="purge">{{ messages.common.cancel }}</el-button>
        <el-button type="primary" :icon="RefreshLeft" :disabled="!canSubmitReview" @click="reviewWorker">{{ text.reviewSubmit }}</el-button>
      </template>
      <template v-else>
      <el-button v-if="canReviewWorker" :icon="RefreshLeft" :disabled="busy" @click="openWorkerReview">{{ text.reviewWorker }}</el-button>
      <el-button v-if="recovery?.recoverySlotHeld && !recovery.cancelRequested" :icon="CircleClose" :disabled="busy" @click="act('cancel')">{{ text.cancel }}</el-button>
      <el-button v-if="canPrepareNext" :disabled="busy" @click="prepareNext">{{ text.newBudget }}</el-button>
      <el-button v-if="recovery?.status === 'ready'" :icon="CircleCheck" :disabled="!canCommit" @click="act('commit')">{{ text.commit }}</el-button>
      <el-button v-if="requestId" :icon="Search" :loading="busy" @click="lookup">{{ text.lookup }}</el-button>
      <el-button v-else type="primary" :icon="CircleCheck" :disabled="!canStart" :loading="busy" @click="start">{{ text.start }}</el-button>
      </template>
      <el-button @click="close">{{ messages.common.close }}</el-button>
    </div></template>
  </el-dialog>
</template>

<style scoped>
.directory-recovery-content { min-width: 0; max-height: min(60vh, 640px); overflow: auto; overflow-wrap: anywhere; padding-right: 4px; }
.directory-recovery-notice { display: flex; gap: 8px; align-items: flex-start; margin: 0 0 12px; padding: 10px 12px; border: 1px solid var(--el-border-color-light); border-radius: 6px; background: var(--el-fill-color-light); color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.directory-recovery-notice .el-icon { flex-shrink: 0; margin-top: 3px; color: var(--el-color-primary); }
.directory-recovery-muted { color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.directory-recovery-error { color: var(--el-color-danger); font-size: 13px; }
.directory-recovery-request { font-size: 13px; color: var(--text-muted); }
.directory-recovery-request code { display: block; margin-top: 6px; color: var(--text-main); }
.directory-recovery-check { display: flex; height: auto; align-items: flex-start; margin: 12px 0; white-space: normal; }
.directory-recovery-check :deep(.el-checkbox__input) { margin-top: 4px; }
.directory-recovery-check :deep(.el-checkbox__label) { white-space: normal; line-height: 1.6; }
.directory-recovery-state, .directory-recovery-footer { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.directory-recovery-state { margin: 12px 0; font-size: 13px; }
.directory-recovery-footer { justify-content: flex-end; }
.directory-recovery-footer .el-button + .el-button { margin-left: 0; }
</style>
