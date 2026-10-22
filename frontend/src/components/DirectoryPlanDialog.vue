<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { CircleClose, DocumentChecked, Refresh, Search } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";
import { cancelDirectoryPlan, findDirectoryPlan, prepareDirectoryPlan, prepareReverseDirectoryPlan, type DirectoryPlan } from "../api/cloudDirectory";
import { inventoryRequestId, InventoryRequestError } from "../api/cloudInventory";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { formatDateTime } from "../utils/displayFormat";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";
import DirectoryExecutionDialog from "./DirectoryExecutionDialog.vue";

const props = defineProps<{ visible: boolean; requestId: string; jobId: string; revision: number;
  entryIds: number[]; folderTerms: string[]; persistRequestId: (id: string) => Promise<void>;
  executionRequestId?: string; persistExecutionRequestId?: (id: string) => Promise<void>;
  recoveryRequestId?: string; persistRecoveryRequestId?: (id: string) => Promise<void>;
  persistReverseRequestId?: (id: string) => Promise<void> }>();
const emit = defineEmits<{ "update:visible": [value: boolean] }>();
const text = messages.cloudInventory.directoryPlan;
const requestId = ref(inventoryRequestId(props.requestId));
const plan = ref<DirectoryPlan | null>(null);
const busy = ref(false);
const errorText = ref("");
const executionVisible = ref(false);
const ownerSession = getSessionGeneration();
let sequence = 0;
let active = true;
const canCreate = computed(() => !busy.value && !requestId.value && props.entryIds.length >= 1 && props.entryIds.length <= 5
  && new Set(props.entryIds).size === props.entryIds.length && props.folderTerms.length > 0);
const reasonText = computed(() => plan.value?.invalidReason ? text.reasons[plan.value.invalidReason] ?? text.invalid : "");
const expiryText = computed(() => plan.value ? formatDateTime(new Date(plan.value.expiresAt * 1000).toISOString()) : "");

function current(token: number) {
  return active && props.visible && token === sequence && getSessionGeneration() === ownerSession;
}

function validPlan(value: DirectoryPlan, expectedRequest: string) {
  return value && inventoryRequestId(value.id) && value.requestId === expectedRequest
    && inventoryRequestId(value.inventoryJobId) && Number.isSafeInteger(value.revision) && value.revision >= 0
    && ["prepared", "cancelled", "expired"].includes(value.status) && typeof value.valid === "boolean"
    && typeof value.parentPath === "string" && value.parentPath.startsWith("/")
    && (value.kind === undefined || ["inventory", "reverse"].includes(value.kind))
    && (value.kind !== "reverse" || (inventoryRequestId(value.originPlanId) && Number.isSafeInteger(value.basisEpoch) && value.basisEpoch! > 0))
    && /^[a-f0-9]{64}$/.test(value.policyHash) && /^[a-f0-9]{64}$/.test(value.rulesHash)
    && Number.isFinite(value.expiresAt) && value.expiresAt > 0 && value.expiresAt < 253402300799
    && Array.isArray(value.items) && value.items.length >= 1 && value.items.length <= 5
    && new Set(value.items.map(item => item.entryId)).size === value.items.length
    && value.items.every(item => Number.isSafeInteger(item.entryId) && item.entryId > 0
      && typeof item.remoteId === "string" && Boolean(item.remoteId) && typeof item.sourceName === "string"
      && typeof item.targetName === "string" && !/[\/\\]/.test(item.sourceName + item.targetName)
      && item.sourcePath === `${value.parentPath}/${item.sourceName}` && item.targetPath === `${value.parentPath}/${item.targetName}`)
    && value.networkAccess === false && value.writeAvailable === false;
}

function failure(error: unknown, token: number) {
  if (!active || token !== sequence || !props.visible) return;
  errorText.value = isSessionChangedError(error) || getSessionGeneration() !== ownerSession ? text.sessionChanged
    : error instanceof InventoryRequestError && error.status === 404 ? text.missing : text.requestFailed;
}

async function submit() {
  if (!canCreate.value || getSessionGeneration() !== ownerSession) return;
  const token = ++sequence;
  const input = { requestId: inventoryRequestId(crypto.randomUUID()), jobId: props.jobId,
    expectedRevision: props.revision, entryIds: [...props.entryIds], folderTerms: [...props.folderTerms] };
  busy.value = true; errorText.value = ""; plan.value = null;
  try {
    if (!input.requestId) throw new Error("invalid_request_id");
    requestId.value = input.requestId;
    await props.persistRequestId(input.requestId);
    if (!current(token)) return;
    const result = await prepareDirectoryPlan(input);
    if (!current(token)) return;
    if (!validPlan(result, input.requestId) || result.inventoryJobId !== input.jobId
        || result.inventoryRevision !== input.expectedRevision || result.items.length !== input.entryIds.length
        || !result.items.every(item => input.entryIds.includes(item.entryId))) {
      errorText.value = text.invalidResponse; return;
    }
    plan.value = result;
  } catch (error) { failure(error, token); }
  finally { if (token === sequence) busy.value = false; }
}

async function lookup() {
  if (!requestId.value || busy.value || getSessionGeneration() !== ownerSession) return;
  const token = ++sequence, id = requestId.value;
  busy.value = true; errorText.value = ""; plan.value = null;
  try {
    const result = await findDirectoryPlan(id);
    if (!current(token)) return;
    if (!validPlan(result, id)) { errorText.value = text.invalidResponse; return; }
    plan.value = result;
  } catch (error) { failure(error, token); }
  finally { if (token === sequence) busy.value = false; }
}

async function cancel() {
  if (busy.value || plan.value?.status !== "prepared" || getSessionGeneration() !== ownerSession) return;
  const token = ++sequence, previous = plan.value;
  busy.value = true; errorText.value = "";
  try {
    const result = await cancelDirectoryPlan(previous.id, previous.revision);
    if (!current(token)) return;
    if (!validPlan(result, requestId.value) || result.id !== previous.id || result.status !== "cancelled"
        || result.revision !== previous.revision + 1) { plan.value = null; errorText.value = text.invalidResponse; return; }
    plan.value = result;
  } catch (error) { if (current(token)) { plan.value = null; failure(error, token); } }
  finally { if (token === sequence) busy.value = false; }
}

async function acknowledge() {
  if (!requestId.value || busy.value || getSessionGeneration() !== ownerSession) return;
  const token = ++sequence;
  busy.value = true;
  try {
    await ElMessageBox.confirm(text.acknowledgeWarning, text.acknowledge, { type: "info",
      confirmButtonText: messages.common.confirm, cancelButtonText: messages.common.cancel });
    if (!current(token)) return;
    const previous = requestId.value;
    requestId.value = "";
    try { await props.persistRequestId(""); } catch (error) { if (current(token)) requestId.value = previous; throw error; }
    if (!current(token)) return;
    requestId.value = ""; plan.value = null;
    emit("update:visible", false);
  } catch (error) { if (error !== "cancel" && error !== "close") failure(error, token); }
  finally { if (token === sequence) busy.value = false; }
}

async function reverse(planId: string, expectedRevision: number) {
  if (busy.value || !plan.value || plan.value.id !== planId || !props.persistReverseRequestId || getSessionGeneration() !== ownerSession) return;
  const previous = plan.value, token = ++sequence;
  busy.value = true; errorText.value = "";
  try {
    await ElMessageBox.confirm(text.reverseWarning, text.reverse, { type: "warning",
      confirmButtonText: messages.common.confirm, cancelButtonText: messages.common.cancel });
    if (!current(token)) return;
    executionVisible.value = false;
    requestId.value = inventoryRequestId(crypto.randomUUID());
    if (!requestId.value) throw new Error("request_id_unavailable");
    plan.value = null;
    await props.persistReverseRequestId(requestId.value);
    if (!current(token)) return;
    const result = await prepareReverseDirectoryPlan(planId, { requestId: requestId.value, expectedRevision });
    if (!current(token)) return;
    if (!validPlan(result, requestId.value) || result.kind !== "reverse" || result.originPlanId !== planId
      || result.items.length > previous.items.length || !result.items.every(item => previous.items.some(old => old.remoteId === item.remoteId
        && old.entryId === item.entryId && old.generation === item.generation && old.targetName === item.sourceName && old.sourceName === item.targetName
        && old.targetPath === item.sourcePath && old.sourcePath === item.targetPath))) {
      errorText.value = text.invalidResponse; return;
    }
    plan.value = result;
  } catch (error) { if (error !== "cancel" && error !== "close") failure(error, token); }
  finally { if (token === sequence) busy.value = false; }
}

function close() {
  executionVisible.value = false;
  sequence += 1; busy.value = false; plan.value = null; errorText.value = "";
  emit("update:visible", false);
}
function updateVisible(value: boolean) { if (!value) close(); }
watch(() => props.visible, value => { sequence += 1; busy.value = false; plan.value = null; errorText.value = ""; executionVisible.value = false;
  if (value) requestId.value = inventoryRequestId(props.requestId); });
watch(() => props.requestId, value => {
  const next = inventoryRequestId(value);
  if (next !== requestId.value) { sequence += 1; busy.value = false; plan.value = null; executionVisible.value = false; requestId.value = next; }
}, { flush: "sync" });
onBeforeUnmount(() => { active = false; sequence += 1; });
</script>

<template>
  <el-dialog :model-value="visible" :title="text.title" width="min(780px, 94vw)" append-to-body destroy-on-close
    :before-close="close" @update:model-value="updateVisible">
    <div class="directory-plan-content">
      <p class="directory-plan-notice">{{ text.localOnly }}</p>
      <p v-if="errorText" class="directory-plan-error" role="alert">{{ errorText }}</p>
      <template v-if="requestId"><label>{{ text.requestId }}</label><code class="directory-plan-id">{{ requestId }}</code></template>
      <template v-if="!requestId">
        <p>{{ formatMessage(text.count, { count: entryIds.length }) }}</p>
        <p class="directory-plan-terms"><strong>{{ text.rules }}</strong> {{ folderTerms.join('、') || text.emptyTerms }}</p>
      </template>
      <template v-if="plan">
        <p v-if="plan.kind === 'reverse'" class="directory-plan-notice">{{ text.reverseHint }}</p>
        <div class="directory-plan-state"><el-tag :type="plan.valid ? 'success' : 'warning'">{{ plan.valid ? text.valid : text.invalid }}</el-tag>
          <span>{{ reasonText }}</span></div>
        <p class="directory-plan-path">{{ plan.parentPath }}</p>
        <el-table :data="plan.items" border class="data-table" max-height="340">
          <el-table-column :label="text.source" prop="sourceName" min-width="220" show-overflow-tooltip />
          <el-table-column :label="text.target" prop="targetName" min-width="220" show-overflow-tooltip />
        </el-table>
        <p class="directory-plan-expiry">{{ text.expires }} {{ expiryText }}</p>
      </template>
    </div>
    <template #footer>
      <div class="directory-plan-footer">
        <el-button v-if="!requestId" type="primary" :icon="DocumentChecked" :disabled="!canCreate" :loading="busy" @click="submit">{{ text.create }}</el-button>
        <el-button v-if="requestId" :icon="plan ? Refresh : Search" :disabled="busy" @click="lookup">{{ text.lookup }}</el-button>
        <el-button v-if="plan?.status === 'prepared' && !executionRequestId && plan.invalidReason !== 'execution_accepted'" :icon="CircleClose" :disabled="busy" @click="cancel">{{ text.cancel }}</el-button>
        <el-button v-if="plan && persistExecutionRequestId" type="primary" :icon="DocumentChecked" :disabled="busy" @click="executionVisible = true">{{ messages.cloudInventory.directoryExecution.open }}</el-button>
        <el-button v-if="requestId && !executionRequestId && !recoveryRequestId" :disabled="busy" @click="acknowledge">{{ text.acknowledge }}</el-button>
        <el-button @click="close">{{ messages.common.close }}</el-button>
      </div>
    </template>
  </el-dialog>
  <DirectoryExecutionDialog v-if="plan && persistExecutionRequestId" v-model:visible="executionVisible" :plan="plan"
    :request-id="executionRequestId || ''" :persist-request-id="persistExecutionRequestId"
    :recovery-request-id="recoveryRequestId" :persist-recovery-request-id="persistRecoveryRequestId"
    :allow-reverse="!!persistReverseRequestId" @reverse="reverse"
    @accepted="plan = { ...plan!, valid: false, invalidReason: 'execution_accepted' }" />
</template>

<style scoped>
.directory-plan-content { min-width: 0; max-height: min(58vh, 560px); overflow: auto; }
.directory-plan-notice { margin: 0 0 16px; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.directory-plan-error { color: var(--el-color-danger); overflow-wrap: anywhere; }
.directory-plan-id { display: block; margin: 6px 0 16px; overflow-wrap: anywhere; }
.directory-plan-terms, .directory-plan-path { overflow-wrap: anywhere; }
.directory-plan-state { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.directory-plan-expiry { font-size: 13px; color: var(--el-text-color-secondary); }
.directory-plan-footer { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.directory-plan-footer .el-button + .el-button { margin-left: 0; }
</style>
