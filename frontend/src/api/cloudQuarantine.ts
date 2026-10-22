import { apiClient } from "./client";
import { inventoryRequestId, InventoryRequestError } from "./cloudInventory";
import { isSessionChangedError } from "../utils/sessionGeneration";
import { zhCnMessages as messages } from "../locales/zh-CN";

const text = messages.cloudInventory.quarantine;
export type QuarantineItem = { entryId:number; generation:number; remoteId:string; sourceName:string; sourcePath:string; targetPath:string; size:number; reason:string; evidence:string[] };
export type QuarantinePlan = { id:string; requestId:string; reviewId:string; inventoryJobId:string; kind:"forward"|"reverse";
  revision:number; status:"prepared"|"cancelled"; valid:boolean; invalidReason:string|null; policyHash:string; snapshotHash:string;
  parentPath:string; targetName:string; items:QuarantineItem[]; createdAt:number; expiresAt:number; executionRequestId:string|null;
  networkAccess:false; writeAvailable:false };
export type QuarantinePolicy = { planAvailable:boolean; reason:string|null; policyHash:string; executionEnabled:boolean; executionImplemented:boolean;
  executionAvailable:boolean; executionReason:string|null; canExecute:boolean; targetName:string; controlledMode:true; atomicIdentityGuaranteed:false;
  limits:{items:number;seconds:number;executionSeconds:number;baseRpc:number;rpcPerItem:number;bytes:number;observeSeconds:number}; networkAccess:false;writeAvailable:false };
export type QuarantineRun = { id:string; requestId:string; planId:string; kind:"move"|"observe"; originalRunId:string|null; latestObservationRequestId:string|null;
  status:"accepted"|"running"|"review"|"cancelled"|"completed"; reason:string|null; revision:number; cancelRequested:boolean;
  globalSlotHeld:boolean; workerSlotHeld:boolean; pollingRecommended:boolean; usedRpc:number;maxRpc:number;receivedBytes:number;maxBytes:number;
  createdAt:number;expiresAt:number;logId:number;logAvailable:boolean;committedEpoch:number|null;
  positions:{ordinal:number;position:string}[];items:{ordinal:number;state:string;reason:string|null}[];
  events:{ordinal:number;method:string;phase:string;responseCode:string|null;receivedBytes:number}[];networkAccess:false;replayed:false };
export type QuarantineHistory = { items:{id:string;requestId:string;kind:"forward"|"reverse";status:string;itemCount:number;createdAt:number;expiresAt:number}[];page:number;pageSize:number;total:number;networkAccess:false;writeAvailable:false };
export type QuarantineStart = { requestId:string;expectedRevision:number;policyHash:string;confirmMaintenance:boolean;confirmGlobalFence:boolean;apiToken:string };
export type QuarantineObserve = { requestId:string;expectedRevision:number;confirmMaintenance:boolean;confirmRemoteIdle:boolean;apiToken:string };
export type QuarantineBudget = { state:"ready"|"paused";pauseReason:string|null;revision:number;cooldownUntil:number };
const uuid = (v:unknown):v is string => typeof v === "string" && !!inventoryRequestId(v);
const integer = (v:unknown, min=0, max=Number.MAX_SAFE_INTEGER):v is number => typeof v === "number" && Number.isSafeInteger(v) && v>=min && v<=max;
const string = (v:unknown,max=4096):v is string => typeof v === "string" && v.length<=max;
const date = (v:unknown):v is number => typeof v === "number" && Number.isFinite(v) && v>=0 && v<=253402300799;
const hash = (v:unknown):v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const endpoint = (id:string) => `/cloud-drive/quarantine-plans/${encodeURIComponent(id)}`;
const runEndpoint = (id:string) => `/cloud-drive/quarantine-runs/${encodeURIComponent(id)}`;

async function safe<T>(action:()=>Promise<T>):Promise<T> {
  try { return await action(); }
  catch(error) {
    if(isSessionChangedError(error)) throw error;
    if(error instanceof InventoryRequestError) throw error;
    const response = (error as {response?:{status?:number;data?:{detail?:unknown}}})?.response;
    const detail = response?.data?.detail;
    const known = text.errors;
    const code = response?.status===404 ? "not_found" : Object.keys(known).find(key => typeof detail==="string"
      ? detail.includes(`：${key}；`) || detail.includes(`'code': '${key}'`) : !!detail && typeof detail==="object" && (detail as {code?:unknown}).code===key);
    throw new InventoryRequestError(code ? known[code] : text.failed,error);
  }
}
function validPlan(v:QuarantinePlan, requestId?:string) {
  if(!v || !uuid(v.id) || !uuid(v.requestId) || (requestId && v.requestId!==requestId) || !uuid(v.reviewId) || !uuid(v.inventoryJobId)
    || !["forward","reverse"].includes(v.kind) || !["prepared","cancelled"].includes(v.status) || !integer(v.revision)
    || typeof v.valid!=="boolean" || (v.invalidReason!==null && !string(v.invalidReason,100)) || v.valid!==(v.invalidReason===null)
    || !hash(v.policyHash) || !hash(v.snapshotHash) || !string(v.parentPath) || !v.parentPath.startsWith("/") || !string(v.targetName,240)
    || !date(v.createdAt) || !date(v.expiresAt) || (v.executionRequestId!==null && !uuid(v.executionRequestId))
    || v.networkAccess!==false || v.writeAvailable!==false || !Array.isArray(v.items) || v.items.length<1 || v.items.length>5
    || new Set(v.items.map(i=>i.entryId)).size!==v.items.length || v.items.some(i=>!integer(i.entryId,1) || !integer(i.generation)
      || !string(i.remoteId,1024) || !i.remoteId || !string(i.sourceName,240) || /[\\/]/.test(i.sourceName)
      || !string(i.sourcePath) || !string(i.targetPath) || !i.sourcePath.startsWith(v.parentPath+"/") || !i.targetPath.startsWith(v.parentPath+"/")
      || !i.sourcePath.endsWith("/"+i.sourceName) || !i.targetPath.endsWith("/"+i.sourceName) || !integer(i.size)
      || !string(i.reason,500) || !Array.isArray(i.evidence) || i.evidence.length>10 || i.evidence.some(e=>!string(e,80)))) throw new Error("invalid_quarantine_plan");
  return v;
}
function validRun(v:QuarantineRun, requestId?:string) {
  if(!v || !uuid(v.id) || !uuid(v.requestId) || (requestId && v.requestId!==requestId) || !uuid(v.planId)
    || !["move","observe"].includes(v.kind) || !["accepted","running","review","cancelled","completed"].includes(v.status)
    || !integer(v.revision) || !integer(v.usedRpc) || !integer(v.maxRpc,1,48) || v.usedRpc>v.maxRpc
    || !integer(v.receivedBytes,0,8*1024*1024) || !integer(v.maxBytes,1,1024*1024) || !date(v.createdAt) || !date(v.expiresAt)
    || !integer(v.logId,1) || typeof v.logAvailable!=="boolean" || typeof v.globalSlotHeld!=="boolean" || typeof v.workerSlotHeld!=="boolean"
    || typeof v.cancelRequested!=="boolean" || typeof v.pollingRecommended!=="boolean" || (v.reason!==null && !string(v.reason,100))
    || (v.originalRunId!==null && !uuid(v.originalRunId)) || (v.latestObservationRequestId!==null && !uuid(v.latestObservationRequestId))
    || (v.committedEpoch!==null && !integer(v.committedEpoch,1)) || !Array.isArray(v.events) || v.events.length>48
    || v.events.some(e=>!integer(e.ordinal,1,48) || !["GetApiTokenInfo","GetRuntimeInfo","FindFileByPath","MoveFile"].includes(e.method)
      || !["sending","responded","unknown"].includes(e.phase) || (v.kind==="observe" && e.method==="MoveFile") || !integer(e.receivedBytes,0,2**31-1))
    || !Array.isArray(v.items) || v.items.length>5 || new Set(v.items.map(i=>i.ordinal)).size!==v.items.length
    || v.items.some(i=>!integer(i.ordinal,1,5) || !["prepared","sending","responded","verified","not_applied","review"].includes(i.state)
      || (i.reason!==null && !string(i.reason,100)))
    || !Array.isArray(v.positions) || v.positions.length>5 || new Set(v.positions.map(p=>p.ordinal)).size!==v.positions.length
    || v.positions.some(p=>!integer(p.ordinal,1,5) || !["at_source","at_target","ambiguous"].includes(p.position))
    || v.networkAccess!==false || v.replayed!==false) throw new Error("invalid_quarantine_run");
  return v;
}
export const prepareQuarantinePlan = (input:{requestId:string;reviewId:string;entryIds:number[];targetEntryId:number}) => safe(async()=>validPlan((await apiClient.post<QuarantinePlan>("/cloud-drive/quarantine-plans",input)).data,input.requestId));
export const findQuarantinePlan = (requestId:string) => safe(async()=>validPlan((await apiClient.get<QuarantinePlan>(`/cloud-drive/quarantine-plan-requests/${encodeURIComponent(requestId)}`)).data,requestId));
export const cancelQuarantinePlan = (id:string,expectedRevision:number) => safe(async()=>validPlan((await apiClient.post<QuarantinePlan>(endpoint(id)+"/cancel",{expectedRevision})).data));
export const findQuarantineRun = (requestId:string) => safe(async()=>validRun((await apiClient.get<QuarantineRun>(`/cloud-drive/quarantine-run-requests/${encodeURIComponent(requestId)}`)).data,requestId));
export const fetchQuarantineRun = (id:string) => safe(async()=>validRun((await apiClient.get<QuarantineRun>(runEndpoint(id))).data));
export const cancelQuarantineRun = (id:string,expectedRevision:number) => safe(async()=>validRun((await apiClient.post<QuarantineRun>(runEndpoint(id)+"/cancel",{expectedRevision})).data));
export const commitQuarantineRun = (id:string,expectedRevision:number) => safe(async()=>validRun((await apiClient.post<QuarantineRun>(runEndpoint(id)+"/commit-local",{expectedRevision,confirmLocalOnly:true})).data));
export const prepareReverseQuarantinePlan = (id:string,requestId:string,expectedRevision:number) => safe(async()=>validPlan((await apiClient.post<QuarantinePlan>(endpoint(id)+"/reverse",{requestId,expectedRevision})).data,requestId));
export const reviewQuarantineWorker = (id:string,expectedRevision:number) => safe(async()=>validRun((await apiClient.post<QuarantineRun>(runEndpoint(id)+"/review-worker",{expectedRevision,confirmClientsStopped:true,confirmRemoteIdle:true})).data));
export async function startQuarantinePlan(id:string,input:QuarantineStart):Promise<QuarantineRun> { return tokenRequest(endpoint(id)+"/execute",input); }
export async function observeQuarantineRun(id:string,input:QuarantineObserve):Promise<QuarantineRun> { return tokenRequest(runEndpoint(id)+"/observe",input); }
async function tokenRequest(path:string,input:QuarantineStart|QuarantineObserve):Promise<QuarantineRun> {
  const body = {...input};
  try { return await safe(async()=>{
    if(typeof location==="undefined" || location.protocol!=="https:") throw new InventoryRequestError(text.https,null);
    return validRun((await apiClient.post<QuarantineRun>(path,body,{timeout:15000})).data,input.requestId);
  }); } finally { body.apiToken=""; input.apiToken=""; }
}
export const fetchQuarantinePolicy = () => safe(async()=>{
  const v = (await apiClient.get<QuarantinePolicy>("/cloud-drive/quarantine/policy")).data;
  if(!v || typeof v.planAvailable!=="boolean" || typeof v.executionEnabled!=="boolean" || typeof v.executionImplemented!=="boolean"
    || typeof v.canExecute!=="boolean" || typeof v.executionAvailable!=="boolean" || v.controlledMode!==true || v.atomicIdentityGuaranteed!==false
    || v.networkAccess!==false || v.writeAvailable!==false || !string(v.targetName,240) || (v.policyHash!=="" && !hash(v.policyHash))
    || !v.limits || !integer(v.limits.items,1,5) || !integer(v.limits.seconds,1,1800) || !integer(v.limits.executionSeconds,1,180)
    || v.limits.baseRpc!==3 || v.limits.rpcPerItem!==9 || !integer(v.limits.bytes,1,1048576)
    || !integer(v.limits.observeSeconds,1,90)) throw new Error("invalid_quarantine_policy");
  return v;
});
export const fetchQuarantineHistory = (page=1) => safe(async()=>{
  const v = (await apiClient.get<QuarantineHistory>(`/cloud-drive/quarantine-plans?page=${page}&pageSize=20`)).data;
  if(!v || v.networkAccess!==false || v.writeAvailable!==false || v.page!==page || v.pageSize!==20 || !integer(v.total,0,1000)
    || !Array.isArray(v.items) || v.items.length>20 || v.items.some(i=>!uuid(i.id) || !uuid(i.requestId) || !integer(i.itemCount,1,5)
      || !["forward","reverse"].includes(i.kind) || !date(i.createdAt))) throw new Error("invalid_quarantine_history");
  return v;
});
export const fetchQuarantineTargets = (reviewId:string,entryId:number) => safe(async()=>{
  const v = (await apiClient.get<{items:{id:number;name:string}[];reason:string|null;networkAccess:false}>(`/cloud-drive/quarantine-targets/${encodeURIComponent(reviewId)}?entryId=${entryId}`)).data;
  if(!v || v.networkAccess!==false || !Array.isArray(v.items) || v.items.length>1 || v.items.some(i=>!integer(i.id,1) || !string(i.name,240))) throw new Error("invalid_quarantine_target");
  return v;
});
export const fetchQuarantineBudget = () => safe(async()=>{
  const v = (await apiClient.get<QuarantineBudget>("/cloud-drive/budget/status")).data;
  if(!v || !["ready","paused"].includes(v.state) || !integer(v.revision) || !date(v.cooldownUntil)) throw new Error("invalid_quarantine_budget");
  return v;
});
export const releaseQuarantineManualPause = (expectedRevision:number) => safe(async()=>{
  await apiClient.post("/cloud-drive/budget/release-directory-manual-pause",{expectedRevision,confirmClientsStopped:true,confirmRemoteIdle:true});
});
