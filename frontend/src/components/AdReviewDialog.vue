<script setup lang="ts">
import { ArrowLeft, ArrowRight, Download, FolderChecked, Plus, Refresh, View } from "@element-plus/icons-vue";
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { findAdReview, listAdReviews, saveAdReview, type AdDecision, type AdReview, type AdReviewHistory } from "../api/cloudAdReview";
import { InventoryRequestError, type InventoryAdviceItem } from "../api/cloudInventory";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";
import { buildAdReviewCsv } from "../utils/cloudAdReviewCsv";
import { formatDateTime } from "../utils/displayFormat";
import TextCell from "./TextCell.vue";
import { useAuthStore } from "../stores/auth";

const props = defineProps<{ visible: boolean; jobId: string; revision: number; items: InventoryAdviceItem[];
  available: boolean; requestId: string; initialHistory?: boolean; persistRequestId: (id: string) => Promise<void> }>();
const emit = defineEmits<{ "update:visible": [value: boolean]; quarantine:[review:AdReview,entryIds:number[]] }>();
const auth=useAuthStore();
const quarantineSelection=ref<number[]>([]);
function quarantineSelectable(item:AdReview["items"][number]){
  return !!visibleResult.value?.sourceCurrent && !busy.value && auth.hasPermission("cloud-quarantine:plan")
    && item.decision==="suggest_quarantine" && !item.protection && !!item.remoteId && item.evidence.length>0;
}
function selectQuarantine(items:AdReview["items"]){quarantineSelection.value=items.filter(quarantineSelectable).map(i=>i.entryId);}
function prepareQuarantine(){
  const review=visibleResult.value,ids=quarantineSelection.value;
  if(!review||busy.value||!props.visible||session!==getSessionGeneration()||ids.length<1||ids.length>5
    ||!ids.every(id=>review.items.some(i=>i.entryId===id&&quarantineSelectable(i))))return;
  emit("quarantine",review,[...ids]);
}
const text = messages.cloudInventory.adReview;
type Draft = { entryId: number; decision: AdDecision; reason: string };
const tab = ref("draft");
const draft = ref<Record<number, Draft>>({});
const selected = ref<number[]>([]);
const confirmed = ref(false);
const busy = ref(false);
const errorText = ref("");
const result = ref<AdReview | null>(null);
const history = ref<AdReviewHistory | null>(null);
const page = ref(1);
let active = true, generation = 0, session = getSessionGeneration();
let ownMarker: string | null = null;
const visibleResult = computed(() => result.value?.requestId === props.requestId ? result.value : null);
watch(visibleResult,()=>{quarantineSelection.value=[];});
const files = computed(() => props.items.filter(item => !item.isDirectory && item.directoryComplete).slice(0, 100));
const maySave = computed(() => props.available && !props.requestId && !busy.value && confirmed.value && selected.value.length > 0
  && selected.value.every(id => {
    const item = draft.value[id];
    return item && (!item.decision.startsWith("suggest_") || !!item.reason.trim());
  }));
const reasonText = (code: string) => messages.cloudInventory.reasons[code as keyof typeof messages.cloudInventory.reasons] ?? code;
const dateText = (date: number) => formatDateTime(new Date(date * 1000).toISOString());
const current = (token: number) => active && props.visible && token === generation && session === getSessionGeneration();
const failureText = (error: unknown) => error instanceof InventoryRequestError ? error.message : text.failed;

function resetDraft() {
  selected.value = []; confirmed.value = false;
  draft.value = Object.fromEntries(files.value.map(item => [item.id, { entryId: item.id, decision: item.protection ? "keep" : "review", reason: "" }]));
}
function selectionChanged(items: InventoryAdviceItem[]) {
  selected.value = items.filter(item => files.value.some(file => file.id === item.id)).map(item => item.id);
  confirmed.value = false;
}
function close() { generation += 1; busy.value = false; emit("update:visible", false); }

async function loadRecord() {
  if (!props.requestId || busy.value || session !== getSessionGeneration()) return;
  const token = ++generation, requestId = props.requestId;
  busy.value = true; errorText.value = ""; result.value = null;
  try {
    const value = await findAdReview(requestId);
    if (current(token) && requestId === props.requestId) result.value = value;
  } catch (error) { if (current(token) && !isSessionChangedError(error)) errorText.value = failureText(error); }
  finally { if (current(token)) busy.value = false; }
}

async function save() {
  if (!maySave.value || session !== getSessionGeneration()) return;
  const token = ++generation, jobId = props.jobId, revision = props.revision;
  const items = selected.value.map(id => ({ ...draft.value[id]!, reason: draft.value[id]!.reason.trim() }));
  busy.value = true; errorText.value = "";
  try {
    const requestId = crypto.randomUUID();
    ownMarker = requestId;
    await props.persistRequestId(requestId);
    await nextTick();
    if (!current(token) || requestId !== props.requestId || jobId !== props.jobId || revision !== props.revision || !props.available) return;
    const value = await saveAdReview({ requestId, jobId, expectedRevision: revision, items, confirmLocalOnly: true });
    if (current(token) && requestId === props.requestId) result.value = value;
  } catch (error) { if (current(token) && !isSessionChangedError(error)) errorText.value = failureText(error); }
  finally { if (current(token)) busy.value = false; }
}

async function loadHistory() {
  if (busy.value || session !== getSessionGeneration()) return;
  const token = ++generation;
  busy.value = true; errorText.value = ""; history.value = null;
  try { const value = await listAdReviews(page.value); if (current(token)) history.value = value; }
  catch (error) { if (current(token) && !isSessionChangedError(error)) errorText.value = failureText(error); }
  finally { if (current(token)) busy.value = false; }
}

async function choose(requestId: string) {
  if (busy.value || !history.value?.items.some(item => item.requestId === requestId)) return;
  const token = ++generation;
  busy.value = true;
  try {
    if (props.requestId && props.requestId !== requestId) await ElMessageBox.confirm(text.newConfirm, text.title, { type: "warning" });
    if (!current(token)) return;
    result.value = null; errorText.value = "";
    ownMarker = requestId;
    await props.persistRequestId(requestId);
    await nextTick();
    if (!current(token) || props.requestId !== requestId) return;
    const value = await findAdReview(requestId);
    if (current(token) && props.requestId === requestId) { result.value = value; tab.value = "draft"; }
  } catch (error) { if (current(token) && error !== "cancel" && error !== "close" && !isSessionChangedError(error)) errorText.value = failureText(error); }
  finally { if (current(token)) busy.value = false; }
}

async function newDraft() {
  if (busy.value || session !== getSessionGeneration()) return;
  const token = ++generation;
  busy.value = true;
  try {
    if (props.requestId) await ElMessageBox.confirm(text.newConfirm, text.title, { type: "warning" });
    if (!current(token)) return;
    ownMarker = "";
    await props.persistRequestId("");
    if (!current(token)) return;
    result.value = null; errorText.value = ""; tab.value = "draft"; resetDraft();
  } catch (error) { if (current(token) && error !== "cancel" && error !== "close" && !isSessionChangedError(error)) errorText.value = text.failed; }
  finally { if (current(token)) busy.value = false; }
}

function exportRecord() {
  const record = visibleResult.value;
  if (!record || !props.visible || busy.value || session !== getSessionGeneration()) return;
  let url: string | undefined;
  try {
    url = URL.createObjectURL(new Blob([buildAdReviewCsv(record)], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `ad-review-${record.id}.csv`;
    document.body.append(link); link.click(); link.remove();
  } catch { errorText.value = text.exportFailed; }
  finally { if (url) { const downloadUrl = url; setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000); } }
}

watch(() => props.visible, value => {
  ownMarker = null;
  generation += 1; busy.value = false; result.value = null; history.value = null; errorText.value = "";
  if (value) {
    session = getSessionGeneration(); page.value = 1; resetDraft();
    tab.value = props.initialHistory ? "history" : "draft";
    if (props.initialHistory) void loadHistory();
    else if (props.requestId) void loadRecord();
  }
}, { immediate: true });
watch(() => [props.jobId, props.revision, props.available, props.items], () => {
  if (!props.visible) return;
  resetDraft();
  // 历史独立于当前观察草稿；来源更新不能使在途历史查询静默失效。
  if (tab.value === "history") return;
  generation += 1; busy.value = false;
  if (props.requestId) void loadRecord();
});
watch(() => props.requestId, value => {
  if (!props.visible) return;
  const ownChange = busy.value && value === ownMarker;
  ownMarker = null;
  if (ownChange) return;
  generation += 1; busy.value = false; result.value = null; errorText.value = "";
  if (value && tab.value === "draft") void loadRecord();
});
watch(tab, value => { if (value === "history" && props.visible) void loadHistory(); });
onUnmounted(() => { active = false; generation += 1; });
</script>

<template>
  <el-dialog :model-value="visible" :title="text.title" width="min(1060px, 96vw)" append-to-body destroy-on-close
    class="ad-review-dialog" @close="close">
    <p class="ad-review-hint">{{ text.localOnly }}</p>
    <p v-if="errorText" class="ad-review-error" role="alert">{{ errorText }}</p>
    <el-tabs v-model="tab">
      <el-tab-pane :label="visibleResult || requestId ? text.result : text.draft" name="draft" :disabled="busy" />
      <el-tab-pane :label="text.history" name="history" :disabled="busy" />
    </el-tabs>
    <div v-loading="busy" class="ad-review-content">
      <template v-if="tab === 'history'">
        <el-table :data="history?.items ?? []" class="data-table" border height="280" :empty-text="text.none">
          <el-table-column :label="text.date" min-width="180"><template #default="{ row }">{{ dateText(row.createdAt) }}</template></el-table-column>
          <el-table-column :label="text.count" width="85" prop="itemCount" align="center" />
          <el-table-column :label="text.source" min-width="210"><template #default="{ row }">{{ row.sourceCurrent ? text.current : text.historical }}</template></el-table-column>
          <el-table-column :label="messages.common.actions" width="68" fixed="right" align="center"><template #default="{ row }">
            <el-tooltip :content="messages.common.detail" :trigger-keys="[]"><el-button class="table-action-button action-view" text :icon="View" :disabled="busy" :aria-label="messages.common.detail" @click="choose(row.requestId)" /></el-tooltip>
          </template></el-table-column>
        </el-table>
        <div class="ad-review-toolbar"><el-button :icon="Refresh" :disabled="busy" @click="loadHistory">{{ messages.common.refresh }}</el-button>
          <el-button :icon="ArrowLeft" :disabled="busy || page <= 1" :aria-label="messages.common.previousPage" @click="page--; loadHistory()" />
          <span>{{ page }}</span><el-button :icon="ArrowRight" :disabled="busy || page * 20 >= (history?.total ?? 0)" :aria-label="messages.common.nextPage" @click="page++; loadHistory()" /></div>
      </template>
      <template v-else-if="visibleResult">
        <p :class="visibleResult.sourceCurrent ? 'ad-review-hint' : 'ad-review-error'" role="status">{{ visibleResult.sourceCurrent ? text.current : text.historical }}</p>
        <p class="ad-review-id">{{ text.recordId }}: {{ visibleResult.id }}</p>
        <el-table :key="visibleResult.id" :data="visibleResult.items" class="data-table" border height="300" @selection-change="selectQuarantine">
          <el-table-column v-if="auth.hasPermission('cloud-quarantine:plan')" type="selection" width="44" :selectable="quarantineSelectable"/>
          <el-table-column :label="messages.cloudInventory.name" min-width="220"><template #default="{ row }"><TextCell :value="row.name" :max-length="45" /></template></el-table-column>
          <el-table-column :label="text.evidence" min-width="190"><template #default="{ row }">{{ row.protection ? reasonText(row.protection) : row.evidence.map(reasonText).join('；') || text.noEvidence }}</template></el-table-column>
          <el-table-column :label="messages.cloudInventory.decision" min-width="185"><template #default="{ row }">{{ text.decisions[row.decision as AdDecision] }}</template></el-table-column>
          <el-table-column :label="text.reason" min-width="220"><template #default="{ row }"><TextCell :value="row.reason || '—'" :max-length="45" /></template></el-table-column>
        </el-table><p class="ad-review-hint">{{ text.immutable }}</p>
      </template>
      <template v-else-if="!requestId">
        <p class="ad-review-hint">{{ formatMessage(text.selected, { count: selected.length }) }}</p>
        <el-table :key="`${jobId}:${revision}`" :data="files" class="data-table" border height="320" :empty-text="text.noFiles" @selection-change="selectionChanged">
          <el-table-column type="selection" width="44" :selectable="() => available && !busy" />
          <el-table-column :label="messages.cloudInventory.name" min-width="230"><template #default="{ row }"><TextCell :value="row.name" :max-length="45" /><p class="ad-review-evidence">{{ row.protection ? reasonText(row.protection) : row.reasons.map(reasonText).join('；') || text.noEvidence }}</p></template></el-table-column>
          <el-table-column :label="messages.cloudInventory.decision" min-width="210"><template #default="{ row }">
            <el-select v-if="draft[row.id]" v-model="draft[row.id]!.decision" :aria-label="`${row.name} ${messages.cloudInventory.decision}`" :disabled="busy || !!row.protection || !selected.includes(row.id)" @change="confirmed = false">
              <el-option v-for="(label, value) in text.decisions" :key="value" :value="value" :label="label" :disabled="value.startsWith('suggest_') && !row.reasons.length" />
            </el-select>
          </template></el-table-column>
          <el-table-column :label="text.reason" min-width="250"><template #default="{ row }"><el-input v-if="draft[row.id]" v-model="draft[row.id]!.reason" :aria-label="`${row.name} ${text.reason}`" maxlength="500" :placeholder="text.reasonPlaceholder" :disabled="busy || !selected.includes(row.id)" @input="confirmed = false" /></template></el-table-column>
        </el-table>
        <el-checkbox v-model="confirmed" :disabled="busy || !available || !selected.length" class="ad-review-confirm">{{ text.confirm }}</el-checkbox>
      </template>
    </div>
    <template #footer><div class="ad-review-toolbar">
      <el-button v-if="requestId && !visibleResult && tab === 'draft'" :icon="Refresh" :disabled="busy" @click="loadRecord">{{ text.lookup }}</el-button>
      <el-button v-if="visibleResult && tab === 'draft'" :icon="Download" :disabled="busy" @click="exportRecord">{{ text.export }}</el-button>
      <el-button v-if="visibleResult&&tab==='draft'&&auth.hasPermission('cloud-quarantine:plan')" :icon="FolderChecked"
        :disabled="busy||!visibleResult.sourceCurrent||quarantineSelection.length<1||quarantineSelection.length>5" @click="prepareQuarantine">{{ messages.cloudInventory.quarantine.prepare }}</el-button>
      <el-button v-if="requestId || tab === 'history'" :icon="Plus" :disabled="busy" @click="newDraft">{{ text.draft }}</el-button>
      <el-button v-if="!requestId && tab === 'draft'" type="primary" :disabled="!maySave" :loading="busy" @click="save">{{ text.save }}</el-button>
      <el-button @click="close">{{ messages.common.close }}</el-button>
    </div></template>
  </el-dialog>
</template>

<style scoped>
.ad-review-hint, .ad-review-evidence, .ad-review-id { color: var(--text-muted); font-size: 13px; overflow-wrap: anywhere; }
.ad-review-error { color: var(--el-color-danger); overflow-wrap: anywhere; }
.ad-review-evidence { margin: 4px 0 0; }
.ad-review-toolbar { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; align-items: center; margin-top: 12px; }
.ad-review-toolbar :deep(.el-button + .el-button) { margin-left: 0; }
.ad-review-confirm { margin-top: 12px; height: auto; white-space: normal; }
.ad-review-confirm :deep(.el-checkbox__label) { white-space: normal; line-height: 1.6; }
.ad-review-content { min-width: 0; max-height: min(58vh, 560px); overflow: auto; }
</style>
