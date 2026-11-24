<script setup lang="ts">
import FullscreenTablePanel from "./FullscreenTablePanel.vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { CircleCheck, CircleClose, InfoFilled, Refresh, Search } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";
import * as api from "../api/cloudDiagnostics";
import { CloudConnectionRequestError } from "../api/cloudConnections";
import { inventoryRequestId } from "../api/cloudInventory";
import { useAuthStore } from "../stores/auth";
import { getSessionGeneration } from "../utils/sessionGeneration";
import { normalizeCloudProtection, normalizeCloudProtectionPage, canReviewCloudReadProcess } from "../utils/cloudProtectionView";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";

const props = defineProps<{ visible: boolean; requestId?: string; managed?: boolean }>();
const emit = defineEmits<{ "update:visible": [value: boolean] }>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudProtections), auth = useAuthStore();
const ownerId = auth.currentUser?.id, ownerSession = getSessionGeneration();
const mode = ref<"owner" | "manager">("owner"), requestDraft = ref("");
const rows = ref<api.CloudProtectionItem[]>([]), page = ref(1), total = ref(0);
const summary = ref<api.CloudReadProtection | null>(null), busy = ref(false), error = ref(""), reloadRequired = ref(false);
const reviewRow = ref<api.CloudProtectionItem | null>(null), clientsStopped = ref(false), remoteIdle = ref(false);
const managerAllowed = computed(() => auth.hasPermission("settings:write"));
let active = true, sequence = 0;
function ownerValid() { return active && props.visible && Boolean(auth.currentUser?.id) && ownerId === auth.currentUser?.id
  && ownerSession === getSessionGeneration() && (typeof document === "undefined" || !document.hidden)
  && (mode.value === "owner" || managerAllowed.value); }
function current(version: number) { return ownerValid() && version === sequence; }
function clearState() {
  sequence++; rows.value = []; total.value = 0; summary.value = null; busy.value = false;
  reviewRow.value = null; clientsStopped.value = false; remoteIdle.value = false; reloadRequired.value = false; error.value = "";
}
function feedback(cause: unknown) { return cause instanceof CloudConnectionRequestError && cause.reasonCode === "storage_not_migrated" ? text.value.notMigrated : text.value.failed; }
async function load(nextPage = page.value) {
  if (!ownerValid() || mode.value !== "manager" || busy.value) return;
  const version = ++sequence;
  rows.value = []; summary.value = null; busy.value = true; error.value = "";
  try {
    const value = normalizeCloudProtectionPage(await api.fetchCloudProtections(nextPage, 20), nextPage);
    if (current(version)) { rows.value = value.items; total.value = value.total; page.value = nextPage; reloadRequired.value = false; }
  } catch (cause) { if (current(version)) { total.value = 0; error.value = feedback(cause); } }
  finally { if (current(version)) busy.value = false; }
}
async function lookup() {
  if (!ownerValid() || busy.value || mode.value !== "owner") return;
  const id = inventoryRequestId(requestDraft.value);
  if (!id) { summary.value = null; error.value = text.value.invalid; return; }
  const version = ++sequence;
  summary.value = null; busy.value = true; error.value = "";
  try {
    const value = normalizeCloudProtection(await api.fetchCloudReadProtection(id), id);
    if (current(version) && requestDraft.value === id) { summary.value = value; reloadRequired.value = false; }
  } catch (cause) { if (current(version)) error.value = feedback(cause); }
  finally { if (current(version)) busy.value = false; }
}
function apply(value: api.CloudReadProtection) {
  summary.value = value;
  rows.value = rows.value.map(row => row.requestId === value.requestId ? { ...row, status: value.status, revision: value.revision,
    workerSlotHeld: value.workerSlotHeld, requestSlotHeld: value.requestSlotHeld, workerPid: value.workerSlotHeld ? row.workerPid : null } : row);
}
async function stop(row: Pick<api.CloudReadProtection, "requestId" | "resourceKind">) {
  if (!ownerValid() || busy.value || reloadRequired.value) return;
  const managed = mode.value === "manager";
  if (managed ? !rows.value.some(value => value.requestId === row.requestId && value.resourceKind === row.resourceKind)
    : !summary.value || summary.value.requestId !== requestDraft.value || summary.value.requestId !== row.requestId) return;
  const version = ++sequence; let sent = false;
  busy.value = true; error.value = "";
  try {
    await ElMessageBox.confirm(text.value.stopConfirm, text.value.stop, { type: "warning", confirmButtonText: text.value.stop, cancelButtonText: messages.value.common.cancel });
    if (!current(version)) return;
    sent = true;
    const value = await (managed ? api.stopManagedCloudIntent(row.requestId) : api.stopCloudReadIntent(row.requestId));
    if (current(version)) apply(normalizeCloudProtection(value, row.requestId, row.resourceKind));
  } catch (cause) {
    if (current(version) && cause !== "cancel" && cause !== "close") { reloadRequired.value = sent; error.value = sent ? text.value.unknown : text.value.failed; }
  } finally { if (current(version)) busy.value = false; }
}
function openReview(row: api.CloudProtectionItem) {
  if (!ownerValid() || mode.value !== "manager" || busy.value || reloadRequired.value || !canReviewCloudReadProcess(row)
      || !rows.value.some(value => value.requestId === row.requestId && value.revision === row.revision)) return;
  reviewRow.value = { ...row }; clientsStopped.value = false; remoteIdle.value = false;
}
async function review() {
  const row = reviewRow.value;
  if (!ownerValid() || mode.value !== "manager" || busy.value || reloadRequired.value || !row
      || !canReviewCloudReadProcess(row) || !clientsStopped.value || !remoteIdle.value) return;
  const version = ++sequence; let sent = false;
  busy.value = true; error.value = "";
  try {
    await ElMessageBox.confirm(text.value.reviewConfirm, text.value.reviewTitle, { type: "warning", confirmButtonText: text.value.review, cancelButtonText: messages.value.common.cancel });
    if (!current(version) || reviewRow.value?.requestId !== row.requestId || reviewRow.value.revision !== row.revision) return;
    sent = true;
    const value = await api.reviewCloudReadWorker(row, { expectedRevision: row.revision, confirmClientsStopped: true, confirmRemoteIdle: true });
    if (current(version)) { apply(normalizeCloudProtection(value, row.requestId, row.resourceKind)); reviewRow.value = null; }
  } catch (cause) {
    if (current(version) && cause !== "cancel" && cause !== "close") { reloadRequired.value = sent; error.value = sent ? text.value.unknown : text.value.failed; reviewRow.value = null; }
  } finally { if (current(version)) busy.value = false; }
}
function changeMode(value: unknown) {
  if (busy.value || (value !== "owner" && value !== "manager") || value === "manager" && !managerAllowed.value) return;
  clearState(); mode.value = value; requestDraft.value = ""; page.value = 1;
  if (value === "manager") void load(1);
}
function draftChanged(value: string) { clearState(); requestDraft.value = value; }
function close() { clearState(); requestDraft.value = ""; emit("update:visible", false); }
function hidden() { if (document.hidden) clearState(); }
watch(() => props.visible, value => {
  clearState(); requestDraft.value = inventoryRequestId(props.requestId); page.value = 1;
  if (!value) return;
  mode.value = props.managed && managerAllowed.value ? "manager" : "owner";
  if (mode.value === "manager") void load();
}, { immediate: true, flush: "sync" });
watch(() => [auth.currentUser?.id, managerAllowed.value], () => { clearState(); requestDraft.value = ""; mode.value = "owner"; }, { flush: "sync" });
onMounted(() => {
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", hidden);
  if (typeof window !== "undefined") window.addEventListener("pagehide", clearState);
});
onBeforeUnmount(() => {
  active = false; clearState();
  if (typeof document !== "undefined") document.removeEventListener("visibilitychange", hidden);
  if (typeof window !== "undefined") window.removeEventListener("pagehide", clearState);
});
</script>

<template>
  <el-dialog :model-value="visible" :title="text.title" width="min(1040px, calc(100vw - 32px))" class="cloud-protection-dialog" append-to-body destroy-on-close
    @update:model-value="(value: boolean) => { if (!value) close(); }">
    <div class="protection-content">
      <p class="protection-notice"><el-icon><InfoFilled /></el-icon><span>{{ text.hint }}</span></p>
      <el-radio-group v-if="managerAllowed" :model-value="mode" :disabled="busy" @update:model-value="changeMode">
        <el-radio-button value="owner">{{ text.owner }}</el-radio-button><el-radio-button value="manager">{{ text.manager }}</el-radio-button>
      </el-radio-group>
      <p v-if="error" class="protection-error" role="alert">{{ error }}</p>
      <p v-if="reloadRequired" class="protection-warning" role="status">{{ text.reload }}</p>
      <el-form v-if="mode === 'owner'" class="protection-lookup" @submit.prevent="lookup">
        <el-input :model-value="requestDraft" :aria-label="text.request" :placeholder="text.request" maxlength="36" autocomplete="off" :disabled="busy" @update:model-value="draftChanged" />
        <el-button :icon="Search" :loading="busy" @click="lookup">{{ text.query }}</el-button>
      </el-form>
      <div v-else class="protection-list">
        <FullscreenTablePanel variant="embedded"><el-table :data="rows" border class="data-table" max-height="min(42vh, 360px)" :empty-text="text.empty">
          <el-table-column prop="requestId" :label="text.request" min-width="280" show-overflow-tooltip />
          <el-table-column :label="text.kind" width="150"><template #default="{ row }">{{ text.kinds[row.resourceKind as api.CloudProtectedKind] }}</template></el-table-column>
          <el-table-column prop="actorId" :label="text.user" width="100" align="center" />
          <el-table-column :label="text.state" width="110"><template #default="{ row }">{{ text.states[row.status as keyof typeof text.states] }}</template></el-table-column>
          <el-table-column :label="text.worker" width="90"><template #default="{ row }">{{ row.workerSlotHeld ? text.held : text.clear }}</template></el-table-column>
          <el-table-column :label="text.slot" width="90"><template #default="{ row }">{{ row.requestSlotHeld ? text.held : text.clear }}</template></el-table-column>
          <el-table-column prop="ownerHost" :label="text.ownerHost" min-width="180" show-overflow-tooltip />
          <el-table-column prop="ownerPid" :label="text.ownerPid" width="110" />
          <el-table-column prop="workerPid" :label="text.workerPid" width="110" />
          <el-table-column :label="messages.common.actions" width="100" fixed="right" align="center"><template #default="{ row }"><div class="table-actions">
            <el-tooltip :content="text.stop" :trigger-keys="[]"><el-button text :icon="CircleClose" class="table-action-button action-delete" :aria-label="text.stop" :disabled="busy || reloadRequired" @click="stop(row)" /></el-tooltip>
            <el-tooltip :content="canReviewCloudReadProcess(row) ? text.review : text.nativeBlocked" :trigger-keys="[]"><span><el-button text :icon="CircleCheck" class="table-action-button action-view" :aria-label="text.review" :disabled="busy || reloadRequired || !canReviewCloudReadProcess(row)" @click="openReview(row)" /></span></el-tooltip>
          </div></template></el-table-column>
        </el-table>
        <div class="protection-pagination"><el-pagination :current-page="page" :total="total" :page-size="20" :pager-count="5" layout="prev, pager, next" :disabled="busy" @current-change="load" />
          <el-button :icon="Refresh" :loading="busy" @click="load()">{{ messages.common.refresh }}</el-button></div>
        </FullscreenTablePanel>
      </div>
      <template v-if="summary">
        <dl class="protection-summary">
          <dt>{{ text.request }}</dt><dd>{{ summary.requestId }}</dd><dt>{{ text.kind }}</dt><dd>{{ text.kinds[summary.resourceKind] }}</dd>
          <dt>{{ text.state }}</dt><dd>{{ text.states[summary.status as keyof typeof text.states] }}</dd>
          <dt>{{ text.worker }}</dt><dd>{{ summary.workerSlotHeld ? text.held : text.clear }}</dd>
          <dt>{{ text.slot }}</dt><dd>{{ summary.requestSlotHeld ? text.held : text.clear }}</dd>
          <dt>{{ text.unresolved }}</dt><dd>{{ summary.unresolved ? text.yes : text.no }}</dd>
          <dt>{{ text.stopped }}</dt><dd>{{ summary.stopRequested ? text.stoppedValue : text.notStopped }}</dd>
          <dt>{{ text.blocked }}</dt><dd>{{ summary.continuationBlocked ? text.yes : text.no }}</dd>
        </dl>
        <el-button v-if="mode === 'owner'" :icon="CircleClose" :disabled="busy || reloadRequired || summary.stopRequested" @click="stop(summary)">{{ text.stop }}</el-button>
      </template>
    </div>
    <template #footer><el-button @click="close">{{ messages.common.close }}</el-button></template>
    <el-dialog :model-value="Boolean(reviewRow)" :title="text.reviewTitle" width="min(560px, calc(100vw - 32px))" append-to-body :close-on-click-modal="false"
      :close-on-press-escape="!busy" :show-close="!busy" @update:model-value="(value: boolean) => { if (!value && !busy) reviewRow = null; }">
      <div class="protection-review"><p>{{ text.reviewHint }}</p><code>{{ reviewRow?.requestId }}</code>
        <el-checkbox v-model="clientsStopped" :disabled="busy">{{ text.clientsStopped }}</el-checkbox>
        <el-checkbox v-model="remoteIdle" :disabled="busy">{{ text.remoteIdle }}</el-checkbox>
      </div>
      <template #footer><el-button :disabled="busy" @click="reviewRow = null">{{ messages.common.cancel }}</el-button>
        <el-button type="primary" :icon="CircleCheck" :disabled="busy || !clientsStopped || !remoteIdle || reloadRequired" @click="review">{{ text.review }}</el-button></template>
    </el-dialog>
  </el-dialog>
</template>

<style scoped>
.protection-content { display: grid; gap: 14px; min-width: 0; }
.protection-notice { display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; margin: 0; color: var(--text-muted); background: var(--panel-soft-bg); border: 1px solid var(--panel-border); border-radius: 6px; font-size: 13px; line-height: 1.6; }
.protection-notice .el-icon { margin-top: 4px; flex-shrink: 0; }
.protection-error { color: var(--el-color-danger); margin: 0; font-size: 13px; }
.protection-warning { color: var(--el-color-warning); margin: 0; font-size: 13px; }
.protection-lookup { display: flex; gap: 8px; min-width: 0; }
.protection-lookup .el-input { min-width: 0; flex: 1; }
.protection-list { min-width: 0; overflow: hidden; }
.protection-pagination { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; margin-top: 12px; }
.protection-summary { display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 8px 12px; margin: 0; font-size: 13px; }
.protection-summary dt { color: var(--text-muted); }
.protection-summary dd { margin: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.protection-content > .el-button { justify-self: start; }
.protection-review { display: grid; gap: 12px; color: var(--text-muted); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.protection-review p { margin: 0; }
.protection-review .el-checkbox { height: auto; align-items: flex-start; margin: 0; }
.protection-review :deep(.el-checkbox__input) { margin-top: 4px; }
.protection-review :deep(.el-checkbox__label) { white-space: normal; line-height: 1.6; }
@media (max-width: 480px) { .protection-lookup { flex-wrap: wrap; } .protection-lookup .el-input { flex-basis: 100%; } .protection-summary { grid-template-columns: minmax(0, 1fr); gap: 4px; } }
</style>
