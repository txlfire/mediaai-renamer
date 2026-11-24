<script setup lang="ts">
import { Check, Connection, Document, Folder, InfoFilled, MagicStick, MoreFilled, Plus, Refresh, Search, View } from "@element-plus/icons-vue";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute, useRouter } from "vue-router";

import { cancelInventoryJob, fetchInventoryAdvice, fetchInventoryJobs, fetchInventorySummary,
  clearInventoryCache, setInventoryKeep, fetchInventoryDirectories, inventoryDirectoryPageSize, inventoryRequestId,
  type InventoryAdviceItem, type InventoryAdvicePage, type InventoryDirectoryPage, type InventoryJobOption, type InventorySummary } from "../api/cloudInventory";
import ListPageLayout from "../components/ListPageLayout.vue";
import FullscreenTablePanel from "../components/FullscreenTablePanel.vue";
import TablePagination from "../components/TablePagination.vue";
import InventoryStartDialog from "../components/InventoryStartDialog.vue";
import DirectoryPlanDialog from "../components/DirectoryPlanDialog.vue";
import DirectoryHistoryDialog from "../components/DirectoryHistoryDialog.vue";
import AdReviewDialog from "../components/AdReviewDialog.vue";
import QuarantineDialog, { type QuarantineSource } from "../components/QuarantineDialog.vue";
import type { AdReview } from "../api/cloudAdReview";
import type { DirectoryPlanHistoryItem } from "../api/cloudDirectory";
import { useAuthStore } from "../stores/auth";
import TextCell from "../components/TextCell.vue";
import CloudTaskSourceSummary from "../components/CloudTaskSourceSummary.vue";
import CloudDiagnosticDialog from "../components/CloudDiagnosticDialog.vue";
import CloudHistoryFilter from "../components/CloudHistoryFilter.vue";
import type { CloudHistoryFilter as HistoryFilter } from "../utils/cloudHistorySource";
import { formatMessage } from "../locales/zh-CN";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";
import { buildCloudInventoryCsv } from "../utils/cloudInventoryCsv";
import { formatDateTime, formatFileSize } from "../utils/displayFormat";
import { useTableRowSelection } from "../utils/tableRowSelection";

const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudInventory);
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const planVisible = ref(false);
const historyVisible = ref(false);
const adReviewVisible = ref(false);
const adReviewHistory = ref(false);
const quarantineVisible=ref(false),quarantineInitialHistory=ref(false);
const quarantineSource=ref<QuarantineSource|null>(null);
const pendingQuarantinePlan=computed(()=>inventoryRequestId(route.query.quarantine_plan_request_id));
const pendingQuarantineRun=computed(()=>inventoryRequestId(route.query.quarantine_run_request_id));
let quarantineOpening=false;
const pendingAdReviewId = computed(() => inventoryRequestId(route.query.ad_review_request_id));
let historyOpening = false;
const pendingPlanRequestId = computed(() => inventoryRequestId(route.query.directory_request_id));
const pendingExecutionRequestId = computed(() => inventoryRequestId(route.query.directory_execution_request_id));
const pendingRecoveryRequestId = computed(() => inventoryRequestId(route.query.directory_recovery_request_id));
const startVisible = ref(false);
const diagnosticVisible = ref(false);
const pendingDiagnosticId = computed(() => inventoryRequestId(route.query.diagnostic_request_id));
const pendingRequestId = computed(() => inventoryRequestId(route.query.inventory_request_id));
const jobs = ref<InventoryJobOption[]>([]);
const totalJobs = ref(0);
const jobPage = ref(1);
const jobsLoading = ref(false);
const historyFilter = ref<HistoryFilter>({});
const ownerSession = getSessionGeneration();
const ownerId = auth.currentUser?.id;
const selectedJobId = ref("");
const summary = ref<InventorySummary | null>(null);
const pageData = ref<InventoryAdvicePage | null>(null);
const afterIds = ref([0]);
const inventoryPageSizeOptions: readonly number[] = [10, 50, 100];
const advicePageSize = ref(100);
const activeTab = ref("all");
const folderTerms = ref<string[]>([]);
const selectedFolders = ref<number[]>([]);
const appliedInputs = ref("");
const loading = ref(false);
const cancelling = ref(false);
const clearing = ref(false);
const keeping = ref(false);
const maintaining = computed(() => clearing.value || keeping.value);
const errorText = ref("");
const detail = ref<InventoryAdviceItem | null>(null);
const detailVisible = ref(false);
const scopeVisible = ref(false);
const scopeTab = ref("scope");
const directoryPage = ref<InventoryDirectoryPage | null>(null);
const directoryPageSize = ref(inventoryDirectoryPageSize);
const directoryAfterIds = ref([0]);
const directoriesLoading = ref(false);
const directoryError = ref("");
const loadedEntryCount = ref(0);
const { table, toggleByRowClick: toggleInventoryRow } = useTableRowSelection<InventoryAdviceItem>();
let active = true;
let sequence = 0;
let readEpoch = 0;
let optionsSequence = 0;
let directorySequence = 0;
let observationSequence = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let pollInFlight = false;
let observationFailed = false;
const inputKey = () => JSON.stringify([folderTerms.value, [...selectedFolders.value].sort((a, b) => a - b)]);
const dirty = computed(() => inputKey() !== appliedInputs.value);
const canPreparePlan = computed(() => !loading.value && !maintaining.value && !dirty.value && summary.value?.scopeComplete
  && summary.value?.pathCurrent !== false
  && selectedFolders.value.length >= 1 && selectedFolders.value.length <= 5 && folderTerms.value.length > 0
  && selectedFolders.value.every(id => pageData.value?.items.some(item => item.id === id && item.folderSuggestion?.status === 'suggested')));
const observationsAvailable = computed(() => summary.value?.cacheState === "available");
const afterId = computed(() => afterIds.value[afterIds.value.length - 1] ?? 0);
const currentAdvicePage = computed(() => pageData.value?.pagination?.page ?? afterIds.value.length);
const viewRows = computed(() => {
  const items = observationsAvailable.value ? pageData.value?.items ?? [] : [];
  return { all: items, folders: items.filter(item => item.isDirectory), review: items.filter(item => item.decision === "review") };
});
const inventoryViews = ["all", "folders", "review"] as const;
const rows = computed(() => viewRows.value[activeTab.value as (typeof inventoryViews)[number]] ?? []);
const emptyText = computed(() => {
  if (summary.value?.cacheState === "cleared") return text.value.clearedEmpty;
  if (summary.value?.cacheState === "clearing") return text.value.clearingEmpty;
  if (activeTab.value === "folders" || activeTab.value === "review") {
    if (!selectedJobId.value) return formatMessage(text.value.chooseTaskForView, { view: text.value[activeTab.value] });
    return activeTab.value === "folders" ? text.value.noFolderRows : text.value.noAdRows;
  }
  return !jobs.value.length ? text.value.noJobs : text.value.noEntries;
});
const canOpenAdReview = computed(() => Boolean(pendingAdReviewId.value) || (observationsAvailable.value && !loading.value
  && !maintaining.value && Boolean(summary.value?.scopeComplete) && summary.value?.pathCurrent !== false));
const mayCancel = computed(() => summary.value && !["completed", "cancelled"].includes(summary.value.status)
  && !summary.value.cancelRequested);
const hasUpdates = computed(() => summary.value && summary.value.storedEntries !== loadedEntryCount.value);
const reasonText = (reason: string | null | undefined) => reason ? text.value.reasons[reason as keyof typeof text.value.reasons] ?? reason : "";
const statusText = (status: InventorySummary["status"]) => text.value.status[status];
const unixTimeText = (value: number | undefined) => value && Number.isFinite(value) ? formatDateTime(new Date(value * 1000).toISOString()) : "--";

function useInventoryAction(command: string) {
  if (!canRead()) return;
  switch (command) {
    case "plan":
      if (auth.hasPermission("cloud-directory:plan") && (pendingPlanRequestId.value || canPreparePlan.value)) planVisible.value = true;
      break;
    case "directory-history":
      if (auth.hasPermission("cloud-directory:plan")) historyVisible.value = true;
      break;
    case "ad-review":
      if (canOpenAdReview.value) { adReviewHistory.value = false; adReviewVisible.value = true; }
      break;
    case "ad-history": adReviewHistory.value = true; adReviewVisible.value = true; break;
    case "quarantine":
      if (auth.hasPermission("cloud-quarantine:plan")) {
        quarantineInitialHistory.value = !(pendingQuarantinePlan.value || pendingQuarantineRun.value);
        quarantineVisible.value = true;
      }
      break;
    case "export": exportPage(); break;
    case "clear": void clearCache(); break;
    case "cancel": void cancelTask(); break;
  }
}
function canRead() {
  return active && ownerId !== undefined && ownerId === auth.currentUser?.id
    && ownerSession === getSessionGeneration() && auth.hasPermission("scan:run")
    && (typeof document === "undefined" || !document.hidden);
}

function currentJob(id: string, token: number) {
  return canRead() && id === selectedJobId.value && token === sequence;
}

function discardDeniedRead(error: unknown) {
  const status = (error as { response?: { status?: number } } | null)?.response?.status;
  // 连接或目录撤权也可能表现为记录不存在，不保留旧路径和结果。
  if (status !== 403 && status !== 404) return false;
  invalidateReadScope();
  return true;
}

function suggestionText(item: InventoryAdviceItem) {
  const suggestion = item.folderSuggestion;
  if (!suggestion) return "--";
  if (dirty.value) return text.value.draft;
  if (suggestion.status === "blocked") return `${text.value.blocked} · ${suggestion.proposedName || reasonText(suggestion.reason)}`;
  return suggestion.proposedName || text.value[suggestion.status as "protected" | "unselected" | "unchanged"] || text.value.noSuggestion;
}

function applySummary(value: InventorySummary) {
  if (!canRead()) return;
  if (summary.value && (value.revision < summary.value.revision || (value.revision === summary.value.revision
      && value.receivedEntries < summary.value.receivedEntries))) return;
  summary.value = value;
  if (!observationsAvailable.value) invalidateObservations();
}

async function persistInventoryRequest(id: string) {
  if (route.query.inventory_request_id === id || (!id && route.query.inventory_request_id === undefined)) return;
  const query = { ...route.query };
  if (id) query.inventory_request_id = id;
  else delete query.inventory_request_id;
  const failure = await router.replace({ query });
  if (failure) throw new Error("request_marker_not_saved");
}

async function persistAdReviewRequest(id: string) {
  if (route.query.ad_review_request_id === id || (!id && route.query.ad_review_request_id === undefined)) return;
  const query = { ...route.query };
  if (id) query.ad_review_request_id = id;
  else delete query.ad_review_request_id;
  const failure = await router.replace({ query });
  if (failure) throw new Error("ad_review_marker_not_saved");
}

async function persistDiagnosticRequest(id: string) {
  if (route.query.diagnostic_request_id === id || (!id && route.query.diagnostic_request_id === undefined)) return;
  const query = { ...route.query };
  if (id) query.diagnostic_request_id = id;
  else delete query.diagnostic_request_id;
  const failure = await router.replace({ query });
  if (failure) throw new Error("diagnostic_marker_not_saved");
}

async function persistQuarantineMarkers(value:{plan:string;run:string}){
  if(pendingQuarantinePlan.value===value.plan&&pendingQuarantineRun.value===value.run)return;
  const query={...route.query};
  if(value.plan)query.quarantine_plan_request_id=value.plan;else delete query.quarantine_plan_request_id;
  if(value.run)query.quarantine_run_request_id=value.run;else delete query.quarantine_run_request_id;
  const failure=await router.replace({query});
  if(failure)throw new Error("quarantine_marker_not_saved");
}
async function openQuarantine(review:AdReview,entryIds:number[]){
  if(!canRead()||quarantineOpening||!auth.hasPermission("cloud-quarantine:plan")||!review.sourceCurrent||entryIds.length<1||entryIds.length>5)return;
  const epoch=readEpoch;quarantineOpening=true;
  const current=()=>canRead()&&epoch===readEpoch&&adReviewVisible.value&&auth.hasPermission("cloud-quarantine:plan");
  try{
    if(pendingQuarantinePlan.value||pendingQuarantineRun.value)
      await ElMessageBox.confirm(text.value.quarantine.switchConfirm,text.value.quarantine.title,{type:"warning"});
    if(!current())return;
    const items=review.items.filter(i=>entryIds.includes(i.entryId)&&i.decision==="suggest_quarantine"&&!i.protection&&!!i.remoteId);
    if(items.length!==entryIds.length)return;
    await persistQuarantineMarkers({plan:"",run:""});
    if(!current())return;
    quarantineSource.value={reviewId:review.id,entryIds:[...entryIds],items:items.map(i=>({entryId:i.entryId,name:i.name,size:i.size})),
      taskBinding:{bindingState:review.bindingState,contextId:review.contextId,source:review.source}};
    adReviewVisible.value=false;quarantineInitialHistory.value=false;quarantineVisible.value=true;
  }catch(error){if(error!=="cancel"&&error!=="close"&&current())errorText.value=text.value.quarantine.failed;}
  finally{quarantineOpening=false;}
}

async function persistDirectoryRequest(id: string) {
  if (route.query.directory_request_id === id || (!id && route.query.directory_request_id === undefined)) return;
  const query = { ...route.query };
  if (id) query.directory_request_id = id;
  else delete query.directory_request_id;
  const failure = await router.replace({ query });
  if (failure) throw new Error("directory_request_marker_not_saved");
}

async function persistDirectoryExecutionRequest(id: string) {
  if (route.query.directory_execution_request_id === id || (!id && route.query.directory_execution_request_id === undefined)) return;
  const query = { ...route.query };
  if (id) query.directory_execution_request_id = id;
  else delete query.directory_execution_request_id;
  const failure = await router.replace({ query });
  if (failure) throw new Error("directory_execution_marker_not_saved");
}

async function persistDirectoryRecoveryRequest(id: string) {
  if (route.query.directory_recovery_request_id === id || (!id && route.query.directory_recovery_request_id === undefined)) return;
  const query = { ...route.query };
  if (id) query.directory_recovery_request_id = id;
  else delete query.directory_recovery_request_id;
  const failure = await router.replace({ query });
  if (failure) throw new Error("directory_recovery_marker_not_saved");
}

async function persistReverseDirectoryRequest(id: string) {
  const query = { ...route.query };
  query.directory_request_id = id;
  delete query.directory_execution_request_id;
  delete query.directory_recovery_request_id;
  const failure = await router.replace({ query });
  if (failure) throw new Error("directory_reverse_marker_not_saved");
}

async function openHistoryRecord(item: DirectoryPlanHistoryItem) {
  if (!canRead() || historyOpening || !auth.hasPermission("cloud-directory:plan")) return;
  const epoch = readEpoch;
  const current = () => canRead() && epoch === readEpoch && historyVisible.value && auth.hasPermission("cloud-directory:plan");
  historyOpening = true;
  try {
    if (pendingPlanRequestId.value && pendingPlanRequestId.value !== item.requestId) {
      await ElMessageBox.confirm(text.value.directoryHistory.switchWarning, text.value.directoryHistory.title, { type: "warning",
        confirmButtonText: messages.value.common.confirm, cancelButtonText: messages.value.common.cancel });
    }
    if (!current()) return;
    const query = { ...route.query };
    query.directory_request_id = item.requestId;
    if (item.executionRequestId) query.directory_execution_request_id = item.executionRequestId;
    else delete query.directory_execution_request_id;
    if (item.recoveryRequestId) query.directory_recovery_request_id = item.recoveryRequestId;
    else delete query.directory_recovery_request_id;
    const failure = await router.replace({ query });
    if (failure) throw new Error("history_marker_not_saved");
    if (!current()) return;
    historyVisible.value = false; planVisible.value = true;
  } catch (error) {
    if (error !== "cancel" && error !== "close" && current()) {
      historyVisible.value = false; errorText.value = text.value.directoryHistory.failed;
    }
  } finally { historyOpening = false; }
}

function showInventoryJob(value: InventorySummary) {
  if (!canRead() || !inventoryRequestId(value.id)) return;
  if (!jobs.value.some(item => item.id === value.id)) {
    jobs.value = [value, ...jobs.value]; totalJobs.value += 1;
  }
  if (selectedJobId.value === value.id) { applySummary(value); void loadCurrent(); }
  else selectedJobId.value = value.id;
}

function invalidateObservations() {
  observationSequence += 1;
  directorySequence += 1;
  pageData.value = null;
  directoryPage.value = null;
  directoriesLoading.value = false;
  directoryAfterIds.value = [0];
  directoryError.value = "";
  detailVisible.value = false;
  detail.value = null;
  table.value?.clearSelection();
  selectedFolders.value = [];
  folderTerms.value = [];
  appliedInputs.value = inputKey();
  loadedEntryCount.value = summary.value?.storedEntries ?? 0;
}

async function loadJobs(more = false) {
  if (!canRead() || jobsLoading.value) return;
  const token = ++optionsSequence;
  jobsLoading.value = true;
  try {
    const nextPage = more ? jobPage.value + 1 : 1;
    const result = historyFilter.value.connectionId ? await fetchInventoryJobs(nextPage, historyFilter.value) : await fetchInventoryJobs(nextPage);
    if (!canRead() || token !== optionsSequence) return;
    jobs.value = more ? [...jobs.value, ...result.items] : result.items;
    totalJobs.value = result.total;
    jobPage.value = result.page;
    if (!selectedJobId.value) {
      const requested = String(route.query.jobId ?? "");
      selectedJobId.value = /^[a-f0-9-]{36}$/.test(requested) ? requested : result.items[0]?.id ?? "";
    }
  } catch (error) {
    if (canRead() && token === optionsSequence && !isSessionChangedError(error)) {
      discardDeniedRead(error);
      errorText.value = text.value.loadFailed;
    }
  } finally {
    if (token === optionsSequence) jobsLoading.value = false;
  }
}

function filterJobs(value: HistoryFilter) {
  if (!canRead() || jobsLoading.value) return;
  optionsSequence++; jobs.value = []; totalJobs.value = 0; jobPage.value = 1; historyFilter.value = { ...value }; void loadJobs();
}

async function loadCurrent(analyze = false, requestedPage = currentAdvicePage.value) {
  if (!canRead() || !selectedJobId.value || loading.value || cancelling.value || maintaining.value || (analyze && !observationsAvailable.value)) return;
  const id = selectedJobId.value;
  const token = ++sequence;
  const observationToken = observationSequence;
  const terms = analyze ? [...folderTerms.value] : [];
  const ids = analyze ? [...selectedFolders.value] : [];
  const key = JSON.stringify([terms, [...ids].sort((a, b) => a - b)]);
  loading.value = true;
  observationFailed = false;
  errorText.value = "";
  detailVisible.value = false;
  scopeVisible.value = false;
  detail.value = null;
  try {
    const status = await fetchInventorySummary(id);
    if (!currentJob(id, token)) return;
    applySummary(status);
    if (!observationsAvailable.value) return;
    const position = status.numberedPagination ? { page: requestedPage } : { afterId: afterId.value };
    const page = await fetchInventoryAdvice(id, { ...position, limit: advicePageSize.value, folderTerms: terms, folderEntryIds: ids });
    if (!currentJob(id, token) || observationToken !== observationSequence || !observationsAvailable.value) return;
    pageData.value = page;
    loadedEntryCount.value = status.storedEntries;
    appliedInputs.value = key;
    if (!analyze) {
      table.value?.clearSelection();
      folderTerms.value = [];
      selectedFolders.value = [];
    }
  } catch (error) {
    if (currentJob(id, token) && observationToken === observationSequence && !isSessionChangedError(error)) {
      discardDeniedRead(error);
      if (!analyze) pageData.value = null;
      observationFailed = true;
      errorText.value = analyze ? text.value.analyzeFailed : text.value.loadFailed;
    }
  } finally {
    if (token === sequence) {
      loading.value = false;
      schedulePoll();
    }
  }
}

function refreshWorkspace() {
  if (!canRead()) return;
  if (!jobs.value.length) void loadJobs();
  if (selectedJobId.value) void loadCurrent();
}

function schedulePoll() {
  clearTimeout(timer);
  if (canRead() && !observationFailed && summary.value && (summary.value.status === "running" || summary.value.workerSlotHeld)) {
    timer = setTimeout(() => { void pollSummary(); }, 1500);
  }
}

async function pollSummary() {
  const id = selectedJobId.value;
  const token = sequence;
  if (!canRead() || !id) return;
  if (pollInFlight || loading.value || maintaining.value) { schedulePoll(); return; }
  pollInFlight = true;
  try {
    const result = await fetchInventorySummary(id);
    if (currentJob(id, token)) applySummary(result);
  } catch (error) {
    if (currentJob(id, token)) {
      discardDeniedRead(error);
      observationFailed = true;
      if (!isSessionChangedError(error)) errorText.value = text.value.loadFailed;
    }
  } finally {
    pollInFlight = false;
    if (currentJob(id, token)) schedulePoll();
  }
}

async function analyzePage() {
  if (!canRead() || loading.value || maintaining.value || !pageData.value || !observationsAvailable.value || summary.value?.pathCurrent === false) return;
  if (folderTerms.value.some(term => term.length < 2 || term.length > 80 || term.trim() !== term || /[\/\\\x00-\x1f\x7f]/.test(term))) {
    errorText.value = text.value.invalidTerms;
    return;
  }
  const id = selectedJobId.value;
  const key = inputKey();
  const observationToken = observationSequence;
  try {
    if (folderTerms.value.length && selectedFolders.value.length) {
      await ElMessageBox.confirm(text.value.confirmFolders, text.value.title, { confirmButtonText: messages.value.common.confirm,
        cancelButtonText: messages.value.common.cancel, type: "info" });
    }
    if (canRead() && id === selectedJobId.value && key === inputKey()
        && observationToken === observationSequence && observationsAvailable.value) await loadCurrent(true);
  } catch (error) {
    if (canRead() && observationToken === observationSequence && error !== "cancel" && error !== "close" && !isSessionChangedError(error)) errorText.value = text.value.analyzeFailed;
  }
}

async function cancelTask() {
  if (!canRead() || !mayCancel.value || loading.value || cancelling.value || maintaining.value) return;
  const id = selectedJobId.value;
  const token = sequence;
  cancelling.value = true;
  try {
    await ElMessageBox.confirm(text.value.cancelConfirm, text.value.cancelTask, { confirmButtonText: messages.value.common.confirm,
      cancelButtonText: messages.value.common.cancel, type: "warning" });
    if (!currentJob(id, token)) return;
    const result = await cancelInventoryJob(id);
    if (!currentJob(id, token)) return;
    applySummary(result);
    if (result.workerSlotHeld) ElMessage.info(text.value.cancelPending);
    schedulePoll();
  } catch (error) {
    if (currentJob(id, token) && error !== "cancel" && error !== "close" && !isSessionChangedError(error)) {
      discardDeniedRead(error);
      errorText.value = text.value.cancelFailed;
    }
  } finally {
    if (token === sequence && id === selectedJobId.value) cancelling.value = false;
  }
}

function changePage(next: boolean) {
  if (!canRead() || loading.value || maintaining.value || !pageData.value) return;
  if (next && pageData.value.hasMore) afterIds.value.push(pageData.value.nextId);
  else if (!next && afterIds.value.length > 1) afterIds.value.pop();
  else return;
  selectedFolders.value = [];
  void loadCurrent();
}

function changeAdvicePageSize(size: number) {
  if (!canRead() || !observationsAvailable.value || loading.value || maintaining.value || cancelling.value
    || size === advicePageSize.value || !inventoryPageSizeOptions.includes(size)) return;
  advicePageSize.value = size;
  afterIds.value = [0];
  selectedFolders.value = [];
  table.value?.clearSelection();
  void loadCurrent(false, 1);
}

function selectFolders(items: InventoryAdviceItem[]) {
  if (!canRead() || !observationsAvailable.value) { selectedFolders.value = []; return; }
  const pageIds = new Set(pageData.value?.items.map(item => item.id) ?? []);
  selectedFolders.value = items.filter(item => pageIds.has(item.id) && item.isDirectory && item.directoryComplete
    && item.folderSuggestion?.status !== "protected").map(item => item.id);
}

function openDetail(item: InventoryAdviceItem) {
  if (!canRead() || !observationsAvailable.value) return;
  detail.value = item; detailVisible.value = true;
}

function openScope() {
  if (!canRead() || !summary.value) return;
  directorySequence += 1;
  directoriesLoading.value = false;
  scopeTab.value = "scope";
  directoryPage.value = null;
  directoryAfterIds.value = [0];
  directoryError.value = "";
  scopeVisible.value = true;
}

async function loadDirectories(requestedPage?: number) {
  if (!canRead() || !scopeVisible.value || !selectedJobId.value || directoriesLoading.value || summary.value?.cacheState !== "available") return;
  const id = selectedJobId.value, token = ++directorySequence;
  directoriesLoading.value = true;
  directoryError.value = "";
  try {
    const page = summary.value.numberedPagination
      ? requestedPage ?? directoryPage.value?.pagination?.page ?? directoryAfterIds.value.length : undefined;
    const after = directoryAfterIds.value[directoryAfterIds.value.length - 1] ?? 0;
    const result = await fetchInventoryDirectories(id, page === undefined ? after : 0, page, directoryPageSize.value);
    if (canRead() && id === selectedJobId.value && token === directorySequence && scopeVisible.value) directoryPage.value = result;
  } catch (error) {
    if (canRead() && id === selectedJobId.value && token === directorySequence && !isSessionChangedError(error)) {
      if (discardDeniedRead(error)) errorText.value = text.value.directoryLoadFailed;
      else {
        directoryPage.value = null;
        directoryError.value = text.value.directoryLoadFailed;
      }
    }
  } finally { if (token === directorySequence) directoriesLoading.value = false; }
}

function changeDirectoryPage(next: boolean) {
  if (!canRead() || directoriesLoading.value || !directoryPage.value) return;
  if (next && directoryPage.value.hasMore) directoryAfterIds.value.push(directoryPage.value.nextId);
  else if (!next && directoryAfterIds.value.length > 1) directoryAfterIds.value.pop();
  else return;
  void loadDirectories();
}

function refreshDirectories() {
  if (!canRead() || directoriesLoading.value) return;
  directoryAfterIds.value = [0];
  void loadDirectories(1);
}

function changeDirectoryPageSize(size: number) {
  if (!canRead() || !scopeVisible.value || !observationsAvailable.value || directoriesLoading.value
    || size === directoryPageSize.value || !inventoryPageSizeOptions.includes(size)) return;
  directoryPageSize.value = size;
  refreshDirectories();
}

function exportPage() {
  if (!canRead() || loading.value || maintaining.value || dirty.value || !observationsAvailable.value || !pageData.value || !rows.value.length) return;
  let url: string | undefined;
  try {
    const csv = buildCloudInventoryCsv(rows.value, pageData.value.ruleVersion, pageData.value.rulesHash);
    url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `inventory-${selectedJobId.value}-${currentAdvicePage.value}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
  } catch { errorText.value = text.value.exportFailed; }
  finally { if (url) { const downloadUrl = url; setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000); } }
}

async function toggleKeep() {
  if (!canRead() || !detail.value || !summary.value || loading.value || maintaining.value || !observationsAvailable.value) return;
  const id = selectedJobId.value, token = sequence, item = detail.value, revision = summary.value.revision;
  const observationToken = observationSequence;
  keeping.value = true;
  try {
    await ElMessageBox.confirm(item.userKept ? text.value.unsetKeepConfirm : text.value.keepConfirm, item.userKept ? text.value.unsetKeep : text.value.keepEntry,
      { confirmButtonText: messages.value.common.confirm, cancelButtonText: messages.value.common.cancel, type: "info" });
    if (!currentJob(id, token)
        || observationToken !== observationSequence || !observationsAvailable.value) return;
    const result = await setInventoryKeep(id, item.id, !item.userKept, revision);
    if (!currentJob(id, token)) return;
    applySummary(result);
    keeping.value = false;
    await loadCurrent();
  } catch (error) {
    if (currentJob(id, token) && observationToken === observationSequence
        && error !== "cancel" && error !== "close" && !isSessionChangedError(error)) {
      discardDeniedRead(error);
      errorText.value = text.value.keepFailed;
    }
  } finally { if (id === selectedJobId.value && token === sequence) keeping.value = false; }
}

async function clearCache() {
  if (!canRead() || !summary.value?.cacheClearAvailable || maintaining.value || loading.value) return;
  const id = selectedJobId.value, token = sequence;
  let revision = summary.value.revision;
  clearing.value = true;
  try {
    await ElMessageBox.confirm(text.value.clearConfirm, text.value.clearCache, { confirmButtonText: messages.value.common.confirm,
      cancelButtonText: messages.value.common.cancel, type: "warning" });
    if (!currentJob(id, token)) return;
    detailVisible.value = false;
    detail.value = null;
    pageData.value = null;
    selectedFolders.value = [];
    folderTerms.value = [];
    appliedInputs.value = inputKey();
    while (currentJob(id, token)) {
      const result = await clearInventoryCache(id, revision);
      if (!currentJob(id, token)) return;
      applySummary(result);
      loadedEntryCount.value = result.storedEntries;
      if (result.cacheState === "cleared") { ElMessage.info(text.value.clearDone); break; }
      if (result.cacheState !== "clearing" || result.revision <= revision) throw new Error("invalid_clear_progress");
      revision = result.revision;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  } catch (error) {
    if (currentJob(id, token) && error !== "cancel" && error !== "close" && !isSessionChangedError(error)) {
      discardDeniedRead(error);
      errorText.value = text.value.clearFailed;
    }
  } finally { if (id === selectedJobId.value && token === sequence) clearing.value = false; }
}

function resetJobRead() {
  sequence += 1;
  observationFailed = false;
  clearTimeout(timer);
  timer = undefined;
  loading.value = false;
  cancelling.value = false;
  clearing.value = false;
  keeping.value = false;
  scopeVisible.value = false;
  summary.value = null;
  invalidateObservations();
  afterIds.value = [0];
  errorText.value = "";
}

function invalidateReadScope() {
  // 只清本地读状态，保留路由中的原请求编号和服务端保护。
  readEpoch += 1;
  optionsSequence += 1;
  jobsLoading.value = false;
  jobs.value = [];
  totalJobs.value = 0;
  jobPage.value = 1;
  startVisible.value = diagnosticVisible.value = planVisible.value = historyVisible.value = false;
  adReviewVisible.value = quarantineVisible.value = false;
  quarantineSource.value = null;
  resetJobRead();
}

function visibilityChanged() {
  if (document.hidden) invalidateReadScope();
}

watch(selectedJobId, () => {
  resetJobRead();
  void loadCurrent();
});
watch([() => auth.currentUser?.id, () => auth.hasPermission("scan:run")], invalidateReadScope, { flush: "sync" });
watch(scopeTab, value => { if (value === "coverage" && !directoryPage.value) void loadDirectories(); });
watch(scopeVisible, value => { if (!value) { directorySequence += 1; directoriesLoading.value = false; } });
onMounted(() => {
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", visibilityChanged);
  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", invalidateReadScope);
    window.addEventListener("mediaai:auth-expired", invalidateReadScope);
  }
  void loadJobs();
});
onUnmounted(() => {
  active = false;
  invalidateReadScope();
  if (typeof document !== "undefined") document.removeEventListener?.("visibilitychange", visibilityChanged);
  if (typeof window !== "undefined") {
    window.removeEventListener("pagehide", invalidateReadScope);
    window.removeEventListener("mediaai:auth-expired", invalidateReadScope);
  }
});
</script>

<template>
  <ListPageLayout class="inventory-page" :title="text.title" :description="text.description" collapsible-filters>
    <template #filters>
      <label class="inventory-label" for="inventory-task">{{ text.task }}</label>
      <el-select id="inventory-task" v-model="selectedJobId" class="inventory-job-select" :placeholder="text.selectTask" :loading="jobsLoading" :aria-label="text.task">
        <el-option v-for="job in jobs" :key="job.id" :value="job.id" :label="`${statusText(job.status)} · ${job.id.slice(0, 8)}`">
          <span>{{ statusText(job.status) }} · {{ job.id.slice(0, 8) }}</span>
        </el-option>
        <template #footer><el-button v-if="jobs.length < totalJobs" text :loading="jobsLoading" @click="loadJobs(true)">{{ text.loadMore }}</el-button></template>
      </el-select>
    </template>
    <template #extraFilters><CloudHistoryFilter class="inventory-history-filter" :disabled="jobsLoading || maintaining" @change="filterJobs" /></template>
    <template #queryAction>
      <el-button :icon="pendingDiagnosticId ? Search : Connection" :disabled="maintaining" @click="diagnosticVisible = true">{{ pendingDiagnosticId ? messages.cloudDiagnostics.lookup : messages.cloudDiagnostics.open }}</el-button>
      <el-button :icon="Refresh" :disabled="maintaining || cancelling" :loading="loading || jobsLoading" @click="refreshWorkspace">{{ messages.common.refresh }}</el-button>
      <el-button :type="pendingRequestId ? 'warning' : 'primary'" :icon="pendingRequestId ? Search : Plus"
        :disabled="maintaining" @click="startVisible = true">{{ pendingRequestId ? text.startFlow.lookup : text.startFlow.open }}</el-button>
    </template>

    <div v-if="errorText" class="inventory-error" role="alert">{{ errorText }}</div>
    <div v-if="summary?.pathCurrent === false" class="inventory-muted" role="status">{{ text.pathOutdated }}</div>
    <div v-if="summary?.cacheState === 'clearing' || summary?.cacheState === 'cleared'" class="inventory-muted" role="status">{{ summary.cacheState === 'cleared' ? text.observationsCleared : text.observationsClearing }}</div>
    <div v-if="summary" class="inventory-scope">
      <el-tag :type="summary.status === 'completed' ? 'success' : summary.status === 'running' ? 'primary' : 'info'">{{ statusText(summary.status) }}</el-tag>
      <span class="inventory-root"><strong>{{ text.scope }}</strong><TextCell :value="summary.selectedPaths[0] || summary.approvedRoot" :max-length="80" /><span v-if="summary.selectedPaths.length > 1" class="inventory-scope-count"> +{{ summary.selectedPaths.length - 1 }}</span></span>
      <el-tooltip :trigger-keys="[]" :content="text.scopeDetails"><el-button class="table-action-button action-view" text :icon="InfoFilled" :aria-label="text.scopeDetails" @click="openScope" /></el-tooltip>
      <span class="inventory-muted">{{ text.stored }} {{ summary.storedEntries }} · {{ text.directories }} {{ summary.completedDirectories }}</span>
      <span v-if="summary.reason" class="inventory-muted">{{ reasonText(summary.reason) }}</span>
      <el-tag v-if="hasUpdates" size="small" type="warning">{{ text.updated }}</el-tag>
    </div>

    <template #table>
      <FullscreenTablePanel>
        <template #actions>
        <div class="inventory-controls">
          <el-tabs v-model="activeTab" class="inventory-tabs workbench-table-tabs">
            <el-tab-pane v-for="view in inventoryViews" :key="view" :name="view"
              :label="pageData ? `${text[view]} (${viewRows[view].length})` : text[view]" />
          </el-tabs>
          <div v-if="activeTab === 'folders' && observationsAvailable && pageData" class="inventory-analysis">
            <el-select v-model="folderTerms" multiple filterable allow-create default-first-option :multiple-limit="50" collapse-tags collapse-tags-tooltip
              :disabled="!observationsAvailable || !pageData || loading || maintaining || summary?.pathCurrent === false" :placeholder="text.termsPlaceholder" :aria-label="text.terms" />
            <el-button :icon="MagicStick" :disabled="!observationsAvailable || !pageData || !pageData.items.length || maintaining || summary?.pathCurrent === false" :loading="loading" @click="analyzePage">{{ text.analyze }}</el-button>
            <el-tag v-if="dirty" size="small" type="info">{{ text.draft }}</el-tag>
          </div>
          <el-dropdown class="inventory-more" trigger="click" @command="useInventoryAction">
            <el-button :icon="MoreFilled">{{ messages.common.more }}</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="auth.hasPermission('cloud-directory:plan') && (summary || pendingPlanRequestId)"
                  command="plan" :disabled="!pendingPlanRequestId && !canPreparePlan" :title="!canPreparePlan ? text.directoryPlan.selectionHint : undefined">{{ pendingPlanRequestId ? text.directoryPlan.lookup : text.directoryPlan.open }}</el-dropdown-item>
                <el-dropdown-item v-if="auth.hasPermission('cloud-directory:plan')" command="directory-history">{{ text.directoryHistory.title }}</el-dropdown-item>
                <el-dropdown-item v-if="summary || pendingAdReviewId" command="ad-review" :disabled="!canOpenAdReview">{{ pendingAdReviewId ? text.adReview.lookup : text.adReview.title }}</el-dropdown-item>
                <el-dropdown-item command="ad-history">{{ text.adReview.history }}</el-dropdown-item>
                <el-dropdown-item v-if="auth.hasPermission('cloud-quarantine:plan')" command="quarantine">{{ text.quarantine.title }}</el-dropdown-item>
                <el-dropdown-item v-if="summary" command="export" divided :disabled="loading || maintaining || dirty || !observationsAvailable || !rows.length">{{ text.exportPage }}</el-dropdown-item>
                <el-dropdown-item v-if="summary && summary.cacheState !== 'cleared'" command="clear"
                  :disabled="loading || keeping || clearing || !summary.cacheClearAvailable" :title="!summary.cacheClearAvailable ? text.clearBlocked : undefined">{{ summary.cacheState === 'clearing' ? text.continueClear : text.clearCache }}</el-dropdown-item>
                <el-dropdown-item v-if="mayCancel" command="cancel" :disabled="maintaining || loading || cancelling">{{ text.cancelTask }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
        </template>
        <el-table :key="`${selectedJobId}:${currentAdvicePage}:${advicePageSize}`" ref="table" v-loading="loading" class="data-table inventory-results-table row-select-table" :data="rows" row-key="id" height="100%"
          :empty-text="emptyText" @row-click="toggleInventoryRow" @selection-change="selectFolders">
          <el-table-column type="selection" width="44" :reserve-selection="true"
            :selectable="(row: InventoryAdviceItem) => summary?.pathCurrent !== false && row.isDirectory && row.directoryComplete && row.folderSuggestion?.status !== 'protected'" />
          <el-table-column :label="text.name" min-width="270">
            <template #default="{ row }">
              <div class="inventory-name"><el-icon><Folder v-if="row.isDirectory" /><Document v-else /></el-icon><TextCell :value="row.name" :max-length="50" /></div>
              <div class="inventory-path"><TextCell :value="row.relativePath" :max-length="70" /></div>
            </template>
          </el-table-column>
          <el-table-column :label="text.completeness" width="125" align="center"><template #default="{ row }"><el-tag size="small" :type="row.directoryComplete ? 'success' : 'warning'">{{ row.directoryComplete ? text.complete : text.partial }}</el-tag></template></el-table-column>
          <el-table-column :label="text.decision" width="130" align="center"><template #default="{ row }"><el-tag size="small" :type="row.decision === 'review' ? 'warning' : 'info'">{{ row.protection ? text.protected : row.decision === 'review' ? text.needsReview : text.keep }}</el-tag></template></el-table-column>
          <el-table-column v-if="activeTab !== 'review'" key="suggestion" :label="text.suggestedName" min-width="230"><template #default="{ row }"><TextCell :value="suggestionText(row)" :max-length="60" /></template></el-table-column>
          <el-table-column v-else key="evidence" :label="text.evidence" min-width="230"><template #default="{ row }"><TextCell :value="row.reasons.map((reason: string) => reasonText(reason)).join(' · ')" :max-length="60" /></template></el-table-column>
          <el-table-column :label="messages.common.actions" fixed="right" width="68" align="center"><template #default="{ row }"><el-tooltip :content="messages.common.detail" :trigger-keys="[]"><el-button class="table-action-button action-view" text :icon="View" :aria-label="messages.common.detail" @click="openDetail(row)" /></el-tooltip></template></el-table-column>
        </el-table>
        <TablePagination :current-page="currentAdvicePage" :page-size="advicePageSize" :page-size-options="inventoryPageSizeOptions" :total="pageData?.pagination?.total"
          :sequential="pageData?.pagination ? undefined : { hasPrevious: afterIds.length > 1, hasNext: Boolean(pageData?.hasMore) }"
          :disabled="loading || maintaining || cancelling || !observationsAvailable" @page-change="(page) => loadCurrent(false, page)"
          @page-size-change="changeAdvicePageSize"
          @previous-page="changePage(false)" @next-page="changePage(true)">
          <template #summary>
            {{ pageData?.pagination ? formatMessage(text.savedTotalRows, { total: pageData.pagination.total }) : formatMessage(text.pageRows, { count: pageData?.items.length ?? 0 }) }}
            <template v-if="pageData && activeTab !== 'all'"> · {{ formatMessage(text.viewRows, { count: rows.length }) }}</template>
            · {{ text.unknownTotal }}
          </template>
        </TablePagination>
      </FullscreenTablePanel>
    </template>

    <el-dialog v-model="detailVisible" :title="text.detailTitle" width="min(620px, 94vw)" destroy-on-close>
      <dl v-if="detail" class="inventory-detail">
        <dt>{{ text.name }}</dt><dd>{{ detail.name }}</dd><dt>{{ text.root }}</dt><dd>{{ detail.relativePath }}</dd>
        <dt>{{ text.completeness }}</dt><dd>{{ detail.directoryComplete ? text.complete : text.partial }}</dd>
        <dt>{{ text.evidence }}</dt><dd>{{ detail.protection ? reasonText(detail.protection) : detail.reasons.length ? detail.reasons.map(reasonText).join('；') : text.evidenceNone }}</dd>
        <template v-if="detail.folderSuggestion"><dt>{{ text.suggestedName }}</dt><dd>{{ dirty ? text.draft : detail.folderSuggestion.proposedName || text.noSuggestion }}<p>{{ reasonText(detail.folderSuggestion.reason) }}</p></dd></template>
      </dl>
      <p class="inventory-muted inventory-caution"><el-icon><InfoFilled /></el-icon>{{ text.conflictUnknown }}</p>
      <template #footer><el-button :icon="Check" :loading="keeping" :disabled="loading || clearing || !observationsAvailable" @click="toggleKeep">{{ detail?.userKept ? text.unsetKeep : text.keepEntry }}</el-button><el-button @click="detailVisible = false">{{ messages.common.close }}</el-button></template>
    </el-dialog>
    <DirectoryPlanDialog v-model:visible="planVisible" :request-id="pendingPlanRequestId" :job-id="selectedJobId"
      :revision="pageData?.jobRevision ?? 0" :entry-ids="selectedFolders" :folder-terms="folderTerms" :persist-request-id="persistDirectoryRequest"
      :execution-request-id="pendingExecutionRequestId" :persist-execution-request-id="persistDirectoryExecutionRequest"
      :recovery-request-id="pendingRecoveryRequestId" :persist-recovery-request-id="persistDirectoryRecoveryRequest"
      :persist-reverse-request-id="persistReverseDirectoryRequest" />
    <DirectoryHistoryDialog v-model:visible="historyVisible" @selected="openHistoryRecord" />
    <AdReviewDialog v-model:visible="adReviewVisible" :job-id="selectedJobId" :revision="pageData?.jobRevision ?? 0"
      :items="pageData?.items ?? []" :available="!!summary?.scopeComplete && observationsAvailable && summary?.pathCurrent !== false && !loading && !maintaining"
      :request-id="pendingAdReviewId" :initial-history="adReviewHistory" :persist-request-id="persistAdReviewRequest" @quarantine="openQuarantine" />
    <QuarantineDialog v-model:visible="quarantineVisible" :source="quarantineSource" :initial-history="quarantineInitialHistory"
      :plan-request-id="pendingQuarantinePlan" :run-request-id="pendingQuarantineRun" :persist-markers="persistQuarantineMarkers"/>
    <el-dialog v-model="scopeVisible" :title="text.scopeDetails" width="min(620px, 94vw)" append-to-body destroy-on-close>
      <div v-if="summary" class="inventory-scope-detail">
        <el-tabs v-model="scopeTab">
          <el-tab-pane :label="text.scope" name="scope">
            <CloudTaskSourceSummary :value="summary" />
            <p class="inventory-muted">{{ text.readOnly }} · {{ text.freshness }}</p>
            <strong>{{ text.root }}</strong><p>{{ summary.approvedRoot }}</p>
            <strong>{{ text.scope }} · {{ summary.mode === 'overview' ? text.overview : text.subtrees }}</strong>
            <ul><li v-for="path in summary.selectedPaths" :key="path">{{ path }}</li></ul>
            <template v-if="summary.excludedPaths.length"><strong>{{ text.exclusions }}</strong><ul><li v-for="path in summary.excludedPaths" :key="path">{{ path }}</li></ul></template>
          </el-tab-pane>
          <el-tab-pane :label="text.budgetDetails" name="budget">
            <dl class="inventory-detail">
              <dt>{{ text.rpcBudget }}</dt><dd>{{ summary.budget?.usedRpc ?? '—' }} / {{ summary.budget?.maxRpc ?? '—' }} · {{ text.remaining }} {{ summary.budget?.remainingRpc ?? '—' }}</dd>
              <dt>{{ text.receiveBudget }}</dt><dd>{{ summary.receivedEntries }} / {{ summary.limits.entries }} · {{ text.remaining }} {{ summary.remainingEntries }}</dd>
              <dt>{{ text.size }}</dt><dd>{{ formatFileSize(summary.receivedBytes) }} / {{ formatFileSize(summary.limits.bytes) }} · {{ text.remaining }} {{ formatFileSize(summary.remainingReceiveBytes) }}</dd>
              <dt>{{ text.cachedBytes }}</dt><dd>{{ formatFileSize(summary.storedBytes) }} / {{ formatFileSize(summary.limits.bytes) }}</dd>
              <dt>{{ text.directoryLimit }}</dt><dd>{{ summary.limits.directories }}</dd>
              <dt>{{ text.depthLimit }}</dt><dd>{{ summary.limits.depth }}<span v-if="summary.mode === 'overview'"> · {{ text.overview }}</span></dd>
              <dt>{{ text.approvalTime }}</dt><dd>{{ unixTimeText(summary.createdAt) }}</dd>
              <dt>{{ text.taskUpdated }}</dt><dd>{{ unixTimeText(summary.updatedAt) }}</dd>
              <dt>{{ text.expiresAt }}</dt><dd>{{ unixTimeText(summary.budget?.expiresAt) }} · {{ summary.budget ? text.approvalStatus[summary.budget.expired ? 'expired' : summary.budget.status] : '—' }}</dd>
            </dl>
            <p class="inventory-muted">{{ text.rpcNotUpstream }}</p><p class="inventory-muted">{{ text.updateNotFreshness }}</p>
          </el-tab-pane>
          <el-tab-pane :label="text.coverage" name="coverage">
            <p class="inventory-muted">{{ text.queueUnknown }}</p>
            <div class="inventory-directory-states"><el-tag v-for="status in (['pending', 'reading', 'partial', 'complete'] as const)" :key="status" :type="status === 'partial' ? 'warning' : status === 'complete' ? 'success' : 'info'">{{ text.directoryStatus[status] }} {{ summary.directoryStates[status] ?? 0 }}</el-tag></div>
            <p v-if="directoryError" class="inventory-error" role="alert">{{ directoryError }}</p>
            <p v-if="summary.cacheState !== 'available'" class="inventory-muted">{{ summary.cacheState === 'cleared' ? text.observationsCleared : text.observationsClearing }}</p>
            <template v-else>
              <FullscreenTablePanel variant="embedded"><el-table v-loading="directoriesLoading" class="data-table inventory-directory-table" :data="directoryPage?.items ?? []" border height="220" :empty-text="text.noEntries">
                <el-table-column :label="text.relativePath" min-width="170"><template #default="{ row }"><TextCell :value="row.relativePath || text.rootDirectory" :max-length="45" /></template></el-table-column>
                <el-table-column :label="text.completeness" width="112" align="center"><template #default="{ row }">{{ text.directoryStatus[row.status as keyof typeof text.directoryStatus] }}</template></el-table-column>
              </el-table></FullscreenTablePanel>
              <TablePagination class="is-inset" :current-page="directoryPage?.pagination?.page ?? directoryAfterIds.length"
                :page-size="directoryPageSize" :page-size-options="inventoryPageSizeOptions" :total="directoryPage?.pagination?.total"
                :sequential="directoryPage?.pagination ? undefined : { hasPrevious: directoryAfterIds.length > 1, hasNext: Boolean(directoryPage?.hasMore) }"
                :disabled="directoriesLoading" @page-change="loadDirectories" @page-size-change="changeDirectoryPageSize"
                @previous-page="changeDirectoryPage(false)" @next-page="changeDirectoryPage(true)">
                <template #actions><el-button :icon="Refresh" :loading="directoriesLoading" @click="refreshDirectories">{{ text.coverageRefresh }}</el-button></template>
                <template #summary>{{ directoryPage?.pagination ? formatMessage(text.savedTotalRows, { total: directoryPage.pagination.total }) : formatMessage(text.pageRows, { count: directoryPage?.items.length ?? 0 }) }} · {{ text.unknownTotal }}</template>
              </TablePagination>
            </template>
          </el-tab-pane>
        </el-tabs>
      </div>
      <template #footer><el-button @click="scopeVisible = false">{{ messages.common.close }}</el-button></template>
    </el-dialog>
    <InventoryStartDialog v-model:visible="startVisible" :request-id="pendingRequestId"
      :persist-request-id="persistInventoryRequest" @observed="showInventoryJob" />
    <CloudDiagnosticDialog v-model:visible="diagnosticVisible" :request-id="pendingDiagnosticId" :persist-request-id="persistDiagnosticRequest" />
  </ListPageLayout>
</template>

<style scoped>
.inventory-page { min-width: 0; }
.inventory-history-filter { width: min(760px, 100%); margin-bottom: 0; }
.inventory-job-select { flex: 1 1 300px !important; max-width: 440px; min-width: 0; }
.inventory-page :deep(.list-filter-primary) { flex: 0 1 500px; }
.inventory-page :deep(.list-query-actions) { margin-inline-start: auto; }
.inventory-label { flex: 0 0 auto; color: var(--text-muted); font-size: 13px; }
.inventory-muted, .inventory-path { color: var(--text-muted); font-size: 13px; }
.inventory-scope { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; min-width: 0; flex-shrink: 0; }
.inventory-root { display: flex; align-items: center; gap: 8px; font-size: 13px; min-width: 0; max-width: min(100%, 600px); }
.inventory-root strong { font-weight: 500; color: var(--text-muted); margin-right: 8px; }
.inventory-root strong, .inventory-scope-count { flex-shrink: 0; }
.inventory-root :deep(.text-cell) { min-width: 0; }
.inventory-scope-detail { max-height: min(55vh, 380px); overflow: auto; overflow-wrap: anywhere; }
.inventory-scope-detail ul { padding-left: 20px; line-height: 1.8; }
.inventory-directory-states { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.inventory-error { color: var(--el-color-danger); font-size: 13px; flex-shrink: 0; }
.inventory-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px; min-width: 0; padding-inline-start: 12px; }
.inventory-tabs { flex: 0 1 auto; min-width: 0; }
.inventory-tabs :deep(.el-tabs__header) { padding-inline: 0; }
.inventory-tabs :deep(.el-tabs__content) { display: none; }
.inventory-analysis { display: flex; align-items: center; flex-wrap: wrap; flex: 1 1 290px; gap: 8px; min-width: 0; }
.inventory-analysis > .el-select { flex: 1 1 160px; max-width: 260px; min-width: 0; }
.inventory-more { margin-inline-start: auto; margin-block-start: 6px; }
.inventory-results-table { container-type: inline-size; }
.inventory-results-table :deep(.el-table__empty-block) { max-inline-size: 100cqi; }
.inventory-name { display: flex; align-items: center; gap: 8px; min-width: 0; }
.inventory-name > .el-icon { flex-shrink: 0; color: var(--text-muted); }
.inventory-path { padding-left: 22px; min-width: 0; }
.inventory-detail { display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 14px; margin: 0; }
.inventory-detail dt { color: var(--text-muted); }
.inventory-detail dd { margin: 0; overflow-wrap: anywhere; }
.inventory-detail p { margin: 6px 0 0; font-size: 13px; color: var(--text-muted); }
.inventory-caution { display: flex; align-items: center; gap: 8px; margin-top: 20px; }
@media (max-height: 650px) { .inventory-page { overflow-y: auto; } .inventory-page :deep(.list-table-card) { min-height: 280px; flex-shrink: 0; } }
@media (max-width: 900px) {
  /* 窄屏页面高度为auto，表格须有确定高度，避免百分比与ResizeObserver相互撑高。 */
  .inventory-page :deep(.list-table-card) { height: max(360px, 60vh); min-height: 360px; flex: 0 0 auto; }
}
@media (max-width: 640px) {
  .inventory-page { overflow-y: auto; }
  .inventory-page :deep(.list-filter-row) { flex-wrap: wrap; overflow: visible; }
  .inventory-page :deep(.list-filter-primary) { flex: 1 1 100%; overflow: visible; }
  .inventory-page :deep(.list-query-actions) { flex: 0 1 auto; flex-wrap: wrap; }
  .inventory-page :deep(.list-filter-actions) { margin-left: auto; }
  .inventory-job-select { max-width: none; }
  .inventory-detail { grid-template-columns: minmax(0, 1fr); gap: 8px; }
  .inventory-detail dt:not(:first-child) { margin-top: 8px; }
}
</style>
