<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { Refresh, Search } from "@element-plus/icons-vue";
import { fetchDirectoryPlanHistory, type DirectoryPlanHistoryItem } from "../api/cloudDirectory";
import { inventoryRequestId } from "../api/cloudInventory";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { formatDateTime } from "../utils/displayFormat";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{ "update:visible": [value: boolean]; selected: [value: DirectoryPlanHistoryItem] }>();
const text = messages.cloudInventory.directoryHistory;
const rows = ref<DirectoryPlanHistoryItem[]>([]), page = ref(1), total = ref(0), busy = ref(false), errorText = ref("");
const ownerSession = getSessionGeneration();
let active = true, sequence = 0;

function status(row: DirectoryPlanHistoryItem) {
  if (row.executionReason === "positions_reconciled") return messages.cloudInventory.directoryExecution.reconciled;
  if (row.executionStatus) return messages.cloudInventory.directoryExecution.status[row.executionStatus];
  return row.status === "cancelled" ? messages.cloudInventory.directoryPlan.cancelled
    : row.expiresAt * 1000 <= Date.now() ? text.expired : text.pending;
}
async function load() {
  if (!props.visible || busy.value || getSessionGeneration() !== ownerSession) return;
  const id = ++sequence;
  busy.value = true; errorText.value = "";
  try {
    const value = await fetchDirectoryPlanHistory(page.value, 20);
    if (!active || !props.visible || id !== sequence || getSessionGeneration() !== ownerSession) return;
    if (value.networkAccess !== false || value.replayed !== false || value.pageSize !== 20 || !Number.isSafeInteger(value.page)
      || value.page < 1 || !Number.isSafeInteger(value.total) || value.total < 0 || value.total > 1000
      || !Array.isArray(value.items) || value.items.length > 20 || new Set(value.items.map(item => item.id)).size !== value.items.length
      || !value.items.every(item => inventoryRequestId(item.id) && inventoryRequestId(item.requestId)
        && ["inventory", "reverse"].includes(item.kind) && typeof item.parentPath === "string" && item.parentPath.startsWith("/")
        && Number.isSafeInteger(item.itemCount) && item.itemCount >= 1 && item.itemCount <= 5
        && Number.isFinite(item.createdAt) && item.createdAt >= 0 && item.createdAt < 253402300799
        && Number.isFinite(item.expiresAt) && item.expiresAt >= 0 && item.expiresAt < 253402300799
        && ["prepared", "cancelled", "expired"].includes(item.status)
        && (item.executionRequestId === null || inventoryRequestId(item.executionRequestId))
        && (item.recoveryRequestId === null || inventoryRequestId(item.recoveryRequestId))
        && (item.executionStatus === null || ["accepted", "running", "review", "cancelled", "completed"].includes(item.executionStatus)))) {
      rows.value = []; errorText.value = text.failed; return;
    }
    rows.value = value.items; page.value = value.page; total.value = value.total;
  } catch (error) {
    if (active && props.visible && id === sequence) {
      rows.value = []; errorText.value = isSessionChangedError(error) ? messages.cloudInventory.directoryPlan.sessionChanged : text.failed;
    }
  } finally { if (id === sequence) busy.value = false; }
}
function choose(row: DirectoryPlanHistoryItem) {
  if (!busy.value && props.visible && getSessionGeneration() === ownerSession && rows.value.some(item => item.id === row.id && item.requestId === row.requestId)) emit("selected", row);
}
function close() { sequence += 1; busy.value = false; rows.value = []; emit("update:visible", false); }
watch(() => props.visible, value => {
  sequence += 1; busy.value = false; rows.value = []; errorText.value = "";
  if (value) { page.value = 1; void load(); }
}, { immediate: true });
onBeforeUnmount(() => { active = false; sequence += 1; });
</script>

<template>
  <el-dialog :model-value="visible" :title="text.title" width="min(1000px, 94vw)" append-to-body destroy-on-close
    @update:model-value="(value: boolean) => { if (!value) close(); }">
    <p class="directory-history-hint">{{ text.hint }}</p>
    <p v-if="errorText" class="directory-history-error" role="alert">{{ errorText }}</p>
    <div class="directory-history-table"><el-table :data="rows" v-loading="busy" border class="data-table" max-height="min(52vh, 480px)" :empty-text="text.empty">
      <el-table-column :label="text.parent" prop="parentPath" min-width="230" show-overflow-tooltip />
      <el-table-column :label="text.kind" width="130"><template #default="{ row }">{{ row.kind === 'reverse' ? text.reverse : text.inventory }}</template></el-table-column>
      <el-table-column :label="text.state" width="120"><template #default="{ row }">{{ status(row) }}</template></el-table-column>
      <el-table-column :label="text.count" prop="itemCount" width="74" align="center" />
      <el-table-column :label="text.created" width="175"><template #default="{ row }">{{ formatDateTime(new Date(row.createdAt * 1000).toISOString()) }}</template></el-table-column>
      <el-table-column :label="messages.common.actions" width="68" fixed="right" align="center"><template #default="{ row }">
        <el-tooltip :content="text.view" :trigger-keys="[]"><el-button class="table-action-button action-view" text :icon="Search" :aria-label="text.view" :disabled="busy" @click="choose(row)" /></el-tooltip>
      </template></el-table-column>
    </el-table></div>
    <template #footer><div class="directory-history-footer">
      <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" :pager-count="5" :disabled="busy" @current-change="load" />
      <el-button :icon="Refresh" :loading="busy" @click="load">{{ messages.common.refresh }}</el-button>
      <el-button @click="close">{{ messages.common.close }}</el-button>
    </div></template>
  </el-dialog>
</template>

<style scoped>
.directory-history-hint { font-size: 13px; color: var(--text-muted); line-height: 1.6; margin: 0 0 12px; }
.directory-history-error { color: var(--el-color-danger); font-size: 13px; }
.directory-history-table { min-width: 0; overflow: hidden; }
.directory-history-footer { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; align-items: center; }
.directory-history-footer .el-pagination { margin-right: auto; }
.directory-history-footer .el-button + .el-button { margin-left: 0; }
</style>
