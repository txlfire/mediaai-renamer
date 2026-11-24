<script setup lang="ts">
import FullscreenTablePanel from "./FullscreenTablePanel.vue";
import TablePagination from "./TablePagination.vue";
import { ArrowLeft, CircleClose, Clock, Document, FolderChecked, MoreFilled, Plus, Refresh, RefreshLeft, Search, View } from "@element-plus/icons-vue";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { useAuthStore } from "../stores/auth";
import { getSessionGeneration, isSessionChangedError } from "../utils/sessionGeneration";
import { InventoryRequestError } from "../api/cloudInventory";
import * as api from "../api/cloudQuarantine";
import { formatMessage } from "../locales/zh-CN";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { formatDateTime, formatFileSize } from "../utils/displayFormat";
import TextCell from "./TextCell.vue";
import OperationLogDrawer from "./OperationLogDrawer.vue";
import CloudTaskSourceSummary from "./CloudTaskSourceSummary.vue";
import CloudHistoryFilter from "./CloudHistoryFilter.vue";
import type { CloudHistoryFilter as HistoryFilter } from "../utils/cloudHistorySource";
import { fetchBoundQuarantinePolicy, startBoundQuarantineMove, startBoundQuarantineObservation } from "../api/cloudBoundActions";
import { resolveCloudTaskBinding, sameCloudTaskSource, type CloudTaskBinding } from "../utils/cloudTaskBinding";

export type QuarantineSource = { reviewId:string;entryIds:number[];items:{entryId:number;name:string;size:number}[];taskBinding?:CloudTaskBinding };
const props = defineProps<{visible:boolean;source:QuarantineSource|null;initialHistory?:boolean;planRequestId:string;runRequestId:string;
  persistMarkers:(value:{plan:string;run:string})=>Promise<void>} >();
const emit = defineEmits<{"update:visible":[value:boolean]}>();
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.cloudInventory.quarantine);
const auth = useAuthStore();
const policy = ref<api.QuarantinePolicy|null>(null);
const plan = ref<api.QuarantinePlan|null>(null);
const run = ref<api.QuarantineRun|null>(null);
const history = ref<api.QuarantineHistory|null>(null);
const historyFilter = ref<HistoryFilter>({});
const targets = ref<{id:number;name:string}[]>([]);
const targetId = ref<number>();
const page = ref(1),tab = ref("current"),busy = ref(false),errorText = ref("");
const localConfirm = ref(false),firstConfirm = ref(false),secondConfirm = ref(false),apiToken = ref("");
const form = ref<"move"|"observe"|"review"|"pause"|null>(null);
const formTarget = ref<api.QuarantineRun|null>(null);
const budget = ref<api.QuarantineBudget|null>(null);
const logsVisible = ref(false);
const isSecure = computed(()=>typeof location!=="undefined" && location.protocol==="https:");
const previewBudget=computed(()=>{
  const limits=policy.value?.limits,count=currentPlan.value?.items.length;
  if(!limits||!count||(form.value!=="move"&&form.value!=="observe"))return "";
  return formatMessage(text.value.budgetPreview,{rpc:form.value==="move"?limits.baseRpc+limits.rpcPerItem*count:6+2*count,
    seconds:form.value==="move"?limits.executionSeconds:limits.observeSeconds,bytes:formatFileSize(limits.bytes)});
});
const currentPlan = computed(()=>plan.value?.requestId===props.planRequestId ? plan.value : null);
const currentRun = computed(()=>run.value?.requestId===props.runRequestId ? run.value : null);
const canPlan = computed(()=>auth.hasPermission("scan:run")&&auth.hasPermission("cloud-quarantine:plan"));
function actionable(value:CloudTaskBinding){try{return resolveCloudTaskBinding(value).mode!=="legacy_unbound";}catch{return false;}}
const hasExecutePermission = computed(()=>canPlan.value&&auth.hasPermission("cloud-quarantine:execute"));
const canExecute = computed(()=>hasExecutePermission.value
  &&(!currentPlan.value||actionable(currentPlan.value))&&(!currentRun.value||actionable(currentRun.value)));
const mayPrepare = computed(()=>canPlan.value&&!busy.value && !props.planRequestId && !props.runRequestId && policy.value?.planAvailable
  && props.source && props.source.entryIds.length>=1 && props.source.entryIds.length<=5 && !!targetId.value && localConfirm.value);
const mayCommit = computed(()=>{
  const value = currentRun.value;
  if(!value || !canExecute.value || value.workerSlotHeld || value.cancelRequested || value.committedEpoch!==null) return false;
  return value.kind==="observe" ? value.reason==="positions_observed" && !value.globalSlotHeld
    : !value.latestObservationRequestId && value.globalSlotHeld && value.items.length===currentPlan.value?.items.length
      && value.items.every(i=>["verified","not_applied"].includes(i.state));
});
const canSubmit = computed(()=>!busy.value && firstConfirm.value && secondConfirm.value && (
  form.value==="move" ? canExecute.value && !!currentPlan.value?.valid && !props.runRequestId && policy.value?.executionAvailable && isSecure.value && !!apiToken.value.trim()
  : form.value==="observe" ? canExecute.value && !!formTarget.value && !!currentPlan.value && isSecure.value && !!apiToken.value.trim()
  : form.value==="review" ? canExecute.value && auth.hasPermission("settings:write") && !!formTarget.value
  : form.value==="pause" ? auth.hasPermission("settings:write") && budget.value?.state==="paused" && budget.value.pauseReason==="manual_pause" : false));
let active = true,generation = 0,session = getSessionGeneration(),ownMarker:string|null = null,pendingToken="";
let timer:ReturnType<typeof setTimeout>|undefined;
const marker = ()=>`${props.planRequestId}|${props.runRequestId}`;
function canRead(){
  return active&&props.visible&&canPlan.value&&session===getSessionGeneration()
    &&(typeof document==="undefined"||!document.hidden);
}
function current(g:number){
  if(!active||!props.visible||g!==generation)return false;
  if(session!==getSessionGeneration()){invalidateReadScope();return false;}
  return canRead();
}
async function readPolicy(record:CloudTaskBinding|null):Promise<api.QuarantinePolicy|null>{
  if(!record)return null;
  const binding=resolveCloudTaskBinding(record);
  if(binding.mode!=="bound")return binding.mode==="single"?api.fetchQuarantinePolicy():null;
  const value=await fetchBoundQuarantinePolicy(binding.source);
  if(value.connectionId!==binding.source.connectionId||value.directoryProfileId!==binding.source.directoryProfileId
    ||value.selectedPath!==binding.source.selectedPath)throw new Error("quarantine_policy_source_mismatch");
  return api.validateQuarantinePolicy(value);
}
function acceptPlan(value:api.QuarantinePlan,expected?:CloudTaskBinding){
  const binding=resolveCloudTaskBinding(value);
  if(expected){const original=resolveCloudTaskBinding(expected);
    if(original.mode==="bound"?!sameCloudTaskSource(value,original.source):binding.mode!==original.mode)
      throw new Error("quarantine_plan_source_mismatch");}
  plan.value=value;
}
function acceptRun(value:api.QuarantineRun,expectedContext?:string){
  const binding=resolveCloudTaskBinding(value),selected=currentPlan.value;
  if(expectedContext&&value.contextId!==expectedContext)throw new Error("quarantine_run_context_mismatch");
  if(selected){const original=resolveCloudTaskBinding(selected);
    if(value.planId!==selected.id||(original.mode==="bound"?!sameCloudTaskSource(value,original.source):binding.mode!==original.mode))
      throw new Error("quarantine_run_source_mismatch");}
  run.value=value;
}
const date = (v:number)=>formatDateTime(new Date(v*1000).toISOString());
const reason = (v:string|null|undefined)=>v ? text.value.errors[v] ?? text.value.states[v] ?? text.value.failed : "";
const state = (v:string)=>text.value.states[v] ?? text.value.failed;
function stopTimer(){ if(timer!==undefined)clearTimeout(timer); timer=undefined; }
function clearSecrets(){apiToken.value="";pendingToken="";firstConfirm.value=false;secondConfirm.value=false;form.value=null;formTarget.value=null;budget.value=null;}
function invalidateHandoff(){generation++;stopTimer();clearSecrets();busy.value=false;ownMarker=null;}
function consumeForm(){const value=apiToken.value.trim();clearSecrets();pendingToken=value;}
function takeToken(){const value=pendingToken;pendingToken="";return value;}
function mayHandoff(g:number,chosen:api.QuarantinePlan){return current(g)&&canExecute.value&&isSecure.value
  &&(typeof document==="undefined"||!document.hidden)&&currentPlan.value?.id===chosen.id&&!!pendingToken;}
watch(hasExecutePermission,value=>{if(!value)invalidateHandoff();},{flush:"sync"});
watch(()=>auth.hasPermission("settings:write"),value=>{if(!value)invalidateHandoff();},{flush:"sync"});
function close(){generation++;stopTimer();clearSecrets();logsVisible.value=false;busy.value=false;emit("update:visible",false);}
async function mark(value:{plan:string;run:string}) {
  if(marker()===`${value.plan}|${value.run}`)return;
  ownMarker=`${value.plan}|${value.run}`;
  await props.persistMarkers(value);
  await nextTick();
  if(marker()!==`${value.plan}|${value.run}`)throw new Error("quarantine_marker_not_saved");
}
function schedule(){
  stopTimer();
  if(canRead() && !busy.value && currentRun.value?.pollingRecommended)
    timer=setTimeout(()=>void loadRun(),1500);
}
async function perform(action:(g:number)=>Promise<void>){
  if(!canRead() || busy.value)return;
  stopTimer();const g=++generation;busy.value=true;errorText.value="";
  let succeeded=false;
  try{await action(g);succeeded=true;}
  catch(error){
    if(current(g)&&!isSessionChangedError(error)){
      if(error instanceof InventoryRequestError&&(error.status===403||error.status===404))invalidateReadScope();
      errorText.value=error instanceof InventoryRequestError ? error.message : text.value.failed;
    }
  }
  finally{if(current(g)){pendingToken="";busy.value=false;if(succeeded)schedule();}}
}
async function loadRun(){
  if(!props.runRequestId)return;
  await perform(async g=>{const id=props.runRequestId;const value=await api.findQuarantineRun(id);if(current(g)&&id===props.runRequestId){
    if(run.value?.id!==value.id)logsVisible.value=false;
    if(!currentPlan.value){
      const original=await api.fetchQuarantinePlan(value.planId);if(!current(g)||id!==props.runRequestId)return;
      await mark({plan:original.requestId,run:id});if(!current(g))return;acceptPlan(original,value);
      const currentPolicy=await readPolicy(original);if(!current(g))return;policy.value=currentPolicy;
    }
    acceptRun(value);
  }});
}
async function loadPlan(){
  if(!props.planRequestId){if(props.runRequestId)await loadRun();return;}
  await perform(async g=>{
    const id=props.planRequestId;run.value=null;logsVisible.value=false;
    const value=await api.findQuarantinePlan(id);
    if(!current(g)||id!==props.planRequestId)return;
    acceptPlan(value);policy.value=null;
    const currentPolicy=await readPolicy(value);if(!current(g)||id!==props.planRequestId)return;policy.value=currentPolicy;
    const runId=props.runRequestId || value.executionRequestId;
    if(runId){await mark({plan:id,run:runId});if(!current(g))return;const execution=await api.findQuarantineRun(runId);
      if(current(g)&&runId===props.runRequestId)acceptRun(execution);
    }
  });
}
async function refresh(){if(props.planRequestId)await loadPlan();else if(props.runRequestId)await loadRun();else await loadSource();}
async function loadHistory(){await perform(async g=>{history.value=null;const value=historyFilter.value.connectionId?await api.fetchQuarantineHistory(page.value,historyFilter.value):await api.fetchQuarantineHistory(page.value);if(current(g))history.value=value;});}
function filterHistory(value:HistoryFilter){if(!canRead()||busy.value)return;
  generation++;history.value=null;page.value=1;historyFilter.value={...value};void loadHistory();}
async function loadSource(){
  await perform(async g=>{
    policy.value=null;
    const p=await readPolicy(props.source?props.source.taskBinding??{}:null);if(!current(g))return;policy.value=p;
    if(!p?.planAvailable || !props.source || props.source.entryIds.length<1 || props.source.entryIds.length>5)return;
    const reviewId=props.source.reviewId,entryId=props.source.entryIds[0]!;
    const value=await api.fetchQuarantineTargets(reviewId,entryId);
    if(current(g)&&props.source?.reviewId===reviewId)targets.value=value.items;
  });
}
async function prepare(){
  if(!mayPrepare.value||!props.source||!targetId.value)return;
  const source=props.source,target=targetId.value;
  await perform(async g=>{const id=crypto.randomUUID();await mark({plan:id,run:""});if(!current(g))return;
    const value=await api.prepareQuarantinePlan({requestId:id,reviewId:source.reviewId,entryIds:[...source.entryIds],targetEntryId:target});
    if(current(g)&&props.planRequestId===id)acceptPlan(value,source.taskBinding??{});
  });
}
async function choose(requestId:string){
  if(!canRead()||busy.value)return;
  if((props.planRequestId||props.runRequestId)&&props.planRequestId!==requestId){
    const before=generation,s=getSessionGeneration();
    try{await ElMessageBox.confirm(text.value.switchConfirm,text.value.history,{type:"warning"});}catch{return;}
    if(!canRead()||before!==generation||s!==getSessionGeneration())return;
  }
  await perform(async g=>{plan.value=null;run.value=null;clearSecrets();logsVisible.value=false;
    await mark({plan:requestId,run:""});if(!current(g))return;
    policy.value=null;
    const value=await api.findQuarantinePlan(requestId);if(!current(g))return;acceptPlan(value);tab.value="current";
    const currentPolicy=await readPolicy(value);if(!current(g))return;policy.value=currentPolicy;
    if(current(g)&&value.executionRequestId){await mark({plan:requestId,run:value.executionRequestId});if(!current(g))return;
      const execution=await api.findQuarantineRun(value.executionRequestId);if(current(g))acceptRun(execution);}
  });
}
async function newPlan(){
  if(busy.value)return;
  const before=generation,s=getSessionGeneration();
  try{await ElMessageBox.confirm(text.value.switchConfirm,text.value.newPlan,{type:"warning"});}catch{return;}
  if(!props.visible||before!==generation||s!==getSessionGeneration())return;
  await perform(async g=>{clearSecrets();plan.value=null;run.value=null;localConfirm.value=false;targetId.value=undefined;
    await mark({plan:"",run:""});if(current(g))tab.value="current";});
  if(props.visible)await loadSource();
}
async function openForm(kind:"move"|"observe"|"review"|"pause"){
  if(busy.value)return;
  clearSecrets();form.value=kind;
  const value=currentRun.value;
  if(kind==="observe"&&value){await perform(async g=>{const original=value.kind==="observe"&&value.originalRunId ? await api.fetchQuarantineRun(value.originalRunId) : value;
    if(current(g))formTarget.value=original;});}
  else if(kind==="review")formTarget.value=value;
  else if(kind==="pause")await perform(async g=>{const value=await api.fetchQuarantineBudget();if(current(g))budget.value=value;});
}
function maintenanceCommand(command:unknown){if(command==="review"||command==="pause")void openForm(command);}
async function submit(){
  if(!canSubmit.value)return;
  if(!props.visible||session!==getSessionGeneration()||(typeof document!=="undefined"&&document.hidden)){invalidateHandoff();return;}
  const kind=form.value,original=formTarget.value,chosen=currentPlan.value,oldRun=currentRun.value;
  const budgetRevision=budget.value?.revision;
  // 消费本次确认；响应不明时只允许按已保存编号查询，不保留可重发的表单。
  consumeForm();
  await perform(async g=>{
    if(kind==="move"&&chosen){
      const id=crypto.randomUUID();await mark({plan:chosen.requestId,run:id});if(!mayHandoff(g,chosen)||props.runRequestId!==id)return;
      run.value=null;
      const binding=resolveCloudTaskBinding(chosen);
      if(binding.mode==="legacy_unbound")throw new Error("quarantine_legacy_readonly");
      const input={requestId:id,expectedRevision:chosen.revision,policyHash:chosen.policyHash,
        confirmMaintenance:true as const,confirmGlobalFence:true as const,apiToken:takeToken()};
      try{
        if(binding.mode==="bound"){
          const receipt=await startBoundQuarantineMove(chosen.id,binding.source,input);if(!current(g)||props.runRequestId!==id)return;
          const value=await api.findQuarantineRun(id);if(current(g)&&props.runRequestId===id)acceptRun(value,receipt.contextId);
        }else{const value=await api.startQuarantinePlan(chosen.id,input);if(current(g)&&props.runRequestId===id)acceptRun(value);}
      }finally{input.apiToken="";}
    }else if(kind==="observe"&&original&&chosen){
      const id=crypto.randomUUID();await mark({plan:chosen.requestId,run:id});if(!mayHandoff(g,chosen)||props.runRequestId!==id)return;
      run.value=null;
      const binding=resolveCloudTaskBinding(original),parentBinding=resolveCloudTaskBinding(chosen);
      if(binding.mode==="legacy_unbound"||(parentBinding.mode==="bound"?!sameCloudTaskSource(original,parentBinding.source):binding.mode!==parentBinding.mode))throw new Error("quarantine_observe_source_mismatch");
      const input={requestId:id,expectedRevision:original.revision,confirmMaintenance:true as const,confirmRemoteIdle:true as const,apiToken:takeToken()};
      try{
        if(binding.mode==="bound"){
          const receipt=await startBoundQuarantineObservation(original.id,binding.source,input);if(!current(g)||props.runRequestId!==id)return;
          const value=await api.findQuarantineRun(id);if(current(g)&&props.runRequestId===id)acceptRun(value,receipt.contextId);
        }else{const value=await api.observeQuarantineRun(original.id,input);if(current(g)&&props.runRequestId===id)acceptRun(value);}
      }finally{input.apiToken="";}
    }else if(kind==="review"&&original){
      const value=await api.reviewQuarantineWorker(original.id,original.revision);if(current(g))acceptRun(value);
    }else if(kind==="pause"&&budgetRevision!==undefined){
      await api.releaseQuarantineManualPause(budgetRevision);
      if(current(g)&&oldRun){const value=await api.fetchQuarantineRun(oldRun.id);if(current(g))acceptRun(value);}
    }
    if(current(g))clearSecrets();
  });
}
async function confirmAction(message:string,action:(g:number)=>Promise<void>){
  if(busy.value)return;
  const before=generation,s=getSessionGeneration();
  try{await ElMessageBox.confirm(message,text.value.title,{type:"warning"});}catch{return;}
  if(!props.visible||before!==generation||s!==getSessionGeneration())return;
  await perform(action);
}
async function commit(){const value=currentRun.value;if(!value||!mayCommit.value)return;
  await confirmAction(text.value.commitConfirm,async g=>{const next=await api.commitQuarantineRun(value.id,value.revision);if(current(g)&&value.requestId===props.runRequestId)acceptRun(next);});}
async function cancel(){const value=currentRun.value,selected=currentPlan.value;
  if(!actionable(value??selected??{}))return;
  await confirmAction(text.value.confirmCancel,async g=>{if(value){const next=await api.cancelQuarantineRun(value.id,value.revision);if(current(g)&&value.requestId===props.runRequestId)acceptRun(next);}
    else if(selected){const next=await api.cancelQuarantinePlan(selected.id,selected.revision);if(current(g)&&selected.requestId===props.planRequestId)acceptPlan(next);}});}
async function reverse(){const selected=currentPlan.value;if(!selected||!currentRun.value?.committedEpoch||!canPlan.value||!actionable(selected))return;
  await confirmAction(text.value.reverseConfirm,async g=>{const requestId=crypto.randomUUID();await mark({plan:requestId,run:""});if(!current(g))return;
    plan.value=null;run.value=null;clearSecrets();
    const value=await api.prepareReverseQuarantinePlan(selected.id,requestId,selected.revision);if(current(g))acceptPlan(value,selected);});}
async function recent(){const id=currentRun.value?.latestObservationRequestId;if(!id)return;
  await perform(async g=>{run.value=null;logsVisible.value=false;await mark({plan:props.planRequestId,run:id});if(!current(g))return;
    const value=await api.findQuarantineRun(id);if(current(g))acceptRun(value);});}
async function original(){const value=currentRun.value;if(!value?.originalRunId)return;
  await perform(async g=>{const next=await api.fetchQuarantineRun(value.originalRunId!);if(!current(g))return;
    await mark({plan:props.planRequestId,run:next.requestId});if(current(g))acceptRun(next);});}
watch(()=>props.visible,value=>{
  generation++;busy.value=false;stopTimer();clearSecrets();ownMarker=null;plan.value=null;run.value=null;history.value=null;targets.value=[];targetId.value=undefined;
  localConfirm.value=false;errorText.value="";logsVisible.value=false;
  if(value){session=getSessionGeneration();page.value=1;historyFilter.value={};tab.value=props.initialHistory?"history":"current";
    if(props.initialHistory)void loadHistory();else if(props.planRequestId||props.runRequestId)void refresh();else void loadSource();}
},{immediate:true});
watch(()=>[props.planRequestId,props.runRequestId],()=>{
  if(!props.visible)return;
  const own=busy.value&&ownMarker===marker();ownMarker=null;if(own)return;
  generation++;busy.value=false;stopTimer();clearSecrets();plan.value=null;run.value=null;logsVisible.value=false;errorText.value="";
  if(props.planRequestId||props.runRequestId)void loadPlan();
});
watch(()=>[props.source?.reviewId,JSON.stringify(props.source?.entryIds??[]),props.source?.taskBinding?.bindingState,
  props.source?.taskBinding?.contextId,props.source?.taskBinding?.source?.connectionId,
  props.source?.taskBinding?.source?.directoryProfileId,props.source?.taskBinding?.source?.selectedPath],()=>{
  if(!props.visible)return;
  invalidateHandoff();
  if(props.planRequestId||props.runRequestId)return;
  policy.value=null;plan.value=null;run.value=null;targets.value=[];targetId.value=undefined;localConfirm.value=false;
  void loadSource();
},{flush:"sync"});
watch(tab,value=>{clearSecrets();if(value==="history"&&props.visible)void loadHistory();else schedule();});
function invalidateReadScope(){
  invalidateHandoff();
  plan.value=null;run.value=null;history.value=null;policy.value=null;targets.value=[];targetId.value=undefined;
  logsVisible.value=false;localConfirm.value=false;errorText.value="";
}
function visibilityChanged(){if(document.hidden)invalidateReadScope();}
watch([()=>auth.currentUser?.id,canPlan],invalidateReadScope,{flush:"sync"});
onMounted(()=>{
  if(typeof document!=="undefined")document.addEventListener("visibilitychange",visibilityChanged);
  if(typeof window!=="undefined"){window.addEventListener("pagehide",invalidateReadScope);window.addEventListener("mediaai:auth-expired",invalidateReadScope);}
});
onUnmounted(()=>{active=false;invalidateHandoff();
  if(typeof document!=="undefined")document.removeEventListener("visibilitychange",visibilityChanged);
  if(typeof window!=="undefined"){window.removeEventListener("pagehide",invalidateReadScope);window.removeEventListener("mediaai:auth-expired",invalidateReadScope);}
});
</script>

<template>
  <el-dialog :model-value="visible" :title="text.title" width="min(1120px, 96vw)" class="quarantine-dialog" append-to-body destroy-on-close @close="close">
    <p class="quarantine-hint">{{ text.safety }}</p>
    <p v-if="errorText" class="quarantine-error" role="alert">{{ errorText }}</p>
    <p v-if="!canPlan" class="quarantine-error" role="status">{{ messages.auth.permissionDenied }}</p>
    <el-tabs v-if="canPlan" v-model="tab"><el-tab-pane :label="text.plan" name="current" :disabled="busy"/><el-tab-pane :label="text.history" name="history" :disabled="busy"/></el-tabs>
    <div v-if="canPlan" v-loading="busy" class="quarantine-content">
      <CloudTaskSourceSummary v-if="tab==='current'&&(currentPlan||currentRun||source?.taskBinding)" :value="currentPlan??currentRun??source?.taskBinding??{}" />
      <template v-if="tab==='history'">
        <CloudHistoryFilter :disabled="busy" @change="filterHistory" />
        <FullscreenTablePanel variant="embedded"><el-table :data="history?.items??[]" class="data-table" border height="300" :empty-text="text.empty">
          <el-table-column :label="messages.cloudHistory.source" min-width="260"><template #default="{row}"><CloudTaskSourceSummary :value="row" compact /></template></el-table-column>
          <el-table-column :label="text.date" min-width="170"><template #default="{row}">{{ date(row.createdAt) }}</template></el-table-column>
          <el-table-column :label="text.direction" width="120"><template #default="{row}">{{ row.kind==='reverse'?text.backward:text.forward }}</template></el-table-column>
          <el-table-column :label="text.count" prop="itemCount" width="90" align="center"/>
          <el-table-column :label="text.status" min-width="120"><template #default="{row}">{{ state(row.status) }}</template></el-table-column>
          <el-table-column :label="messages.common.actions" width="70" fixed="right" align="center"><template #default="{row}"><el-tooltip :content="messages.common.detail" :trigger-keys="[]"><el-button text class="table-action-button action-view" :icon="View" :aria-label="messages.common.detail" :disabled="busy" @click="choose(row.requestId)"/></el-tooltip></template></el-table-column>
        </el-table>
        <TablePagination class="is-inset" :current-page="page" :total="history?.total ?? 0" :fixed-page-size="20" :disabled="busy"
          @page-change="(nextPage) => { page = nextPage; loadHistory(); }">
          <template #actions><el-button :icon="Refresh" :disabled="busy" @click="loadHistory">{{ messages.common.refresh }}</el-button></template>
        </TablePagination>
        </FullscreenTablePanel>
      </template>
      <template v-else-if="form">
        <h3>{{ form==='move'?text.execute:form==='observe'?text.observe:form==='review'?text.reviewWorker:text.releasePause }}</h3>
        <p v-if="form==='move'||form==='observe'" class="quarantine-hint">{{ formatMessage(text.tokenScope,{path:currentPlan?.parentPath??''}) }}</p>
        <p v-if="previewBudget" class="quarantine-hint">{{ previewBudget }}</p>
        <p v-if="form==='move'||form==='observe'" class="quarantine-hint">{{ form==='move'?text.movePermissions:text.observePermissions }}</p>
        <p v-if="form==='pause'" class="quarantine-hint">{{ text.pauseConfirm }}</p>
        <el-form label-position="top" class="quarantine-form">
          <el-form-item v-if="form==='move'||form==='observe'" :label="text.token">
            <el-input v-if="isSecure" v-model="apiToken" type="password" autocomplete="off" :aria-label="text.token" maxlength="8192" :disabled="busy"/>
            <p v-else class="quarantine-error" role="alert">{{ text.https }}</p>
          </el-form-item>
          <p v-if="form==='move'||form==='observe'" class="quarantine-hint">{{ text.tokenHint }}</p>
          <el-checkbox v-model="firstConfirm" :disabled="busy">{{ form==='move'||form==='observe'?text.maintenance:text.clientsStopped }}</el-checkbox>
          <el-checkbox v-model="secondConfirm" :disabled="busy">{{ form==='move'?text.globalFence:text.remoteIdle }}</el-checkbox>
        </el-form>
      </template>
      <template v-else>
        <template v-if="currentPlan">
          <div class="quarantine-summary"><el-tag :type="currentPlan.valid?'success':'warning'">{{ currentPlan.valid?text.current:reason(currentPlan.invalidReason) }}</el-tag><span>{{ currentPlan.kind==='reverse'?text.backward:text.forward }}</span><span>{{ text.expired }}: {{ date(currentPlan.expiresAt) }}</span></div>
          <p class="quarantine-id">{{ text.requestId }}: {{ currentPlan.requestId }}</p>
          <FullscreenTablePanel variant="embedded"><el-table :data="currentPlan.items" class="data-table" border height="230">
            <el-table-column :label="text.source" min-width="210"><template #default="{row}"><TextCell :value="row.sourceName" :max-length="40"/></template></el-table-column>
            <el-table-column :label="text.sourcePath" min-width="260"><template #default="{row}"><TextCell :value="row.sourcePath" :max-length="48"/></template></el-table-column>
            <el-table-column :label="text.targetPath" min-width="260"><template #default="{row}"><TextCell :value="row.targetPath" :max-length="48"/></template></el-table-column>
          </el-table></FullscreenTablePanel>
        </template>
        <template v-else-if="!planRequestId&&!runRequestId">
          <p>{{ source?formatMessage(text.selected,{count:source.entryIds.length}):text.noSelection }}</p>
          <FullscreenTablePanel variant="embedded" v-if="source"><el-table :data="source.items" class="data-table" border height="210"><el-table-column :label="text.source" min-width="210"><template #default="{row}"><TextCell :value="row.name" :max-length="50"/></template></el-table-column><el-table-column :label="messages.cloudInventory.size" width="130"><template #default="{row}">{{ formatFileSize(row.size) }}</template></el-table-column></el-table></FullscreenTablePanel>
          <el-form label-position="top" class="quarantine-form"><el-form-item :label="text.destination"><el-select v-model="targetId" :aria-label="text.destination" :disabled="busy||!targets.length"><el-option v-for="target in targets" :key="target.id" :value="target.id" :label="target.name"/></el-select></el-form-item><p v-if="source&&!targets.length&&!busy" class="quarantine-hint">{{ text.targetMissing }}</p><el-checkbox v-model="localConfirm" :disabled="busy||!targets.length">{{ text.planConfirm }}</el-checkbox></el-form>
        </template>
        <template v-if="currentRun">
          <div class="quarantine-summary"><strong>{{ state(currentRun.status) }}</strong><el-tag :type="currentRun.globalSlotHeld?'warning':'success'">{{ currentRun.globalSlotHeld?text.protected:text.released }}</el-tag><span>{{ text.budget }} {{ currentRun.usedRpc }}/{{ currentRun.maxRpc }}</span><span>{{ text.bytes }} {{ formatFileSize(currentRun.receivedBytes) }}</span></div>
          <el-progress :percentage="Math.round(currentRun.usedRpc/currentRun.maxRpc*100)" :show-text="false"/>
          <p class="quarantine-id">{{ text.requestId }}: {{ currentRun.requestId }}</p>
          <p v-if="currentRun.reason" class="quarantine-hint" role="status">{{ currentRun.committedEpoch && currentRun.status==='review' ? text.originalUnknown : reason(currentRun.reason) }}</p>
          <FullscreenTablePanel variant="embedded"><el-table :data="currentRun.items.length?currentRun.items:currentRun.positions" class="data-table" border height="150"><el-table-column label="#" width="65" prop="ordinal"/><el-table-column :label="text.status" min-width="170"><template #default="{row}">{{ state(row.state??row.position) }}</template></el-table-column></el-table></FullscreenTablePanel>
          <p v-if="!currentRun.logAvailable" class="quarantine-hint">{{ text.logsMissing }}</p>
        </template>
        <p v-if="policy&&!policy.executionEnabled" class="quarantine-hint">{{ text.disabled }}</p>
      </template>
    </div>
    <template #footer><div class="quarantine-actions">
      <template v-if="tab==='current'&&form"><el-button :disabled="busy" @click="clearSecrets">{{ text.back }}</el-button><el-button type="primary" :disabled="!canSubmit" :loading="busy" @click="submit">{{ text.submit }}</el-button></template>
      <template v-else-if="tab==='current'">
        <el-button :icon="Refresh" :disabled="busy" @click="refresh">{{ planRequestId||runRequestId?text.lookup:text.refreshPolicy }}</el-button>
        <el-button v-if="!planRequestId&&!runRequestId" type="primary" :icon="FolderChecked" :disabled="!mayPrepare" @click="prepare">{{ text.save }}</el-button>
        <el-button v-if="currentPlan?.valid&&!runRequestId" type="primary" :icon="FolderChecked" :disabled="busy||!canExecute||!policy?.executionAvailable" @click="openForm('move')">{{ text.execute }}</el-button>
        <el-button v-if="currentRun?.globalSlotHeld&&currentRun.kind==='move'&&!currentRun.workerSlotHeld&&currentRun.status==='review'" :icon="Search" :disabled="busy||!canExecute" @click="openForm('observe')">{{ text.observe }}</el-button>
        <el-button v-if="mayCommit" type="primary" :icon="Document" :disabled="busy" @click="commit">{{ text.commit }}</el-button>
        <el-button v-if="currentRun?.committedEpoch" :icon="RefreshLeft" :disabled="busy||!currentPlan||!canPlan||!actionable(currentPlan)" @click="reverse">{{ text.reverse }}</el-button>
        <el-button v-if="currentRun?.latestObservationRequestId" :icon="Clock" :disabled="busy" @click="recent">{{ text.latestObserve }}</el-button>
        <el-button v-if="currentRun?.originalRunId" :icon="ArrowLeft" :disabled="busy" @click="original">{{ text.original }}</el-button>
        <el-button v-if="(currentRun&&['accepted','running'].includes(currentRun.status))||(currentPlan?.valid&&!runRequestId)" :icon="CircleClose" :disabled="busy||!actionable(currentRun??currentPlan??{})" @click="cancel">{{ currentRun?text.cancel:text.cancelPlan }}</el-button>
        <el-button v-if="currentRun" :icon="Document" :disabled="busy||!currentRun.logAvailable||!auth.hasPermission('audit:read')" @click="logsVisible=true">{{ text.logs }}</el-button>
        <el-button v-if="planRequestId||runRequestId" :icon="Plus" :disabled="busy" @click="newPlan">{{ text.newPlan }}</el-button>
        <el-dropdown v-if="currentRun&&auth.hasPermission('settings:write')" trigger="click" :disabled="busy" @command="maintenanceCommand"><el-button :icon="MoreFilled" :aria-label="text.maintenanceTools" :disabled="busy"/><template #dropdown><el-dropdown-menu><el-dropdown-item command="review">{{ text.reviewWorker }}</el-dropdown-item><el-dropdown-item command="pause">{{ text.releasePause }}</el-dropdown-item></el-dropdown-menu></template></el-dropdown>
      </template>
      <el-button @click="close">{{ messages.common.close }}</el-button>
    </div></template>
  </el-dialog>
  <OperationLogDrawer v-model:visible="logsVisible" task-type="cloud_quarantine_run" :task-id="currentRun?.logId??null" :title="text.logs"/>
</template>

<style scoped>
.quarantine-content { min-width:0; max-height:min(58vh,590px); overflow:auto; }
.quarantine-hint,.quarantine-id { color:var(--text-muted);font-size:13px;overflow-wrap:anywhere; }
.quarantine-error { color:var(--el-color-danger);overflow-wrap:anywhere; }
.quarantine-summary,.quarantine-actions { display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;margin:12px 0;min-width:0; }
.quarantine-summary { font-size:13px;font-variant-numeric:tabular-nums; }
.quarantine-actions { justify-content:flex-end;margin-bottom:0; }
.quarantine-actions :deep(.el-button+.el-button) { margin-left:0; }
.quarantine-form { margin-top:16px;min-width:0; }
.quarantine-form .el-select { width:100%; }
.quarantine-form :deep(.el-checkbox) { display:flex;height:auto;white-space:normal;margin:12px 0;align-items:flex-start; }
.quarantine-form :deep(.el-checkbox__label) { white-space:normal;line-height:1.6; }
.quarantine-form :deep(.el-checkbox__input) { margin-top:4px; }
h3 { font-size:16px; }
</style>
