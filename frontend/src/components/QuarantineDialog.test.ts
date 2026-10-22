import { createRenderer,h,nextTick,reactive,ssrContextKey } from "vue";
import { afterEach,beforeEach,describe,expect,it,vi } from "vitest";
import { plan,planId,policy,run,runId,nextId } from "../testSupport/cloudQuarantine";
import { invalidateSessionRequests } from "../utils/sessionGeneration";
import type { QuarantinePlan,QuarantineRun,QuarantineHistory } from "../api/cloudQuarantine";
const mocks=vi.hoisted(()=>({policy:vi.fn(),plan:vi.fn(),run:vi.fn(),history:vi.fn(),targets:vi.fn(),prepare:vi.fn(),start:vi.fn(),observe:vi.fn(),
  fetch:vi.fn(),commit:vi.fn(),cancel:vi.fn(),cancelPlan:vi.fn(),reverse:vi.fn(),review:vi.fn(),budget:vi.fn(),release:vi.fn(),permission:vi.fn(),confirm:vi.fn()}));
vi.mock("../api/cloudQuarantine",()=>({fetchQuarantinePolicy:mocks.policy,findQuarantinePlan:mocks.plan,findQuarantineRun:mocks.run,
  fetchQuarantineHistory:mocks.history,fetchQuarantineTargets:mocks.targets,prepareQuarantinePlan:mocks.prepare,startQuarantinePlan:mocks.start,
  observeQuarantineRun:mocks.observe,fetchQuarantineRun:mocks.fetch,commitQuarantineRun:mocks.commit,cancelQuarantineRun:mocks.cancel,
  cancelQuarantinePlan:mocks.cancelPlan,prepareReverseQuarantinePlan:mocks.reverse,reviewQuarantineWorker:mocks.review,
  fetchQuarantineBudget:mocks.budget,releaseQuarantineManualPause:mocks.release}));
vi.mock("element-plus",()=>({ElMessageBox:{confirm:mocks.confirm}}));
vi.mock("../stores/auth",()=>({useAuthStore:()=>({hasPermission:mocks.permission})}));
vi.mock("./OperationLogDrawer.vue",()=>({default:{render:()=>null}}));
import QuarantineDialog,{type QuarantineSource} from "./QuarantineDialog.vue";

type Node={children:Node[]};
const renderer=createRenderer<Node,Node>({createElement:()=>({children:[]}),createText:()=>({children:[]}),createComment:()=>({children:[]}),
  insert:(node,parent)=>{parent.children.push(node);},remove:()=>{},patchProp:()=>{},setText:()=>{},setElementText:()=>{},parentNode:()=>null,nextSibling:()=>null});
type Props={visible:boolean;source:QuarantineSource|null;initialHistory:boolean;planRequestId:string;runRequestId:string;persistMarkers:(value:{plan:string;run:string})=>Promise<void>};
type State={plan:QuarantinePlan|null;run:QuarantineRun|null;currentPlan:QuarantinePlan|null;history:QuarantineHistory|null;busy:boolean;errorText:string;
  form:string|null;apiToken:string;firstConfirm:boolean;secondConfirm:boolean;localConfirm:boolean;targetId:number|undefined;logsVisible:boolean;
  mayPrepare:boolean;mayCommit:boolean;canSubmit:boolean;tab:string;prepare:()=>Promise<void>;openForm:(kind:string)=>Promise<void>;submit:()=>Promise<void>;
  loadRun:()=>Promise<void>;loadHistory:()=>Promise<void>;commit:()=>Promise<void>;choose:(id:string)=>Promise<void>;close:()=>void};
let app:ReturnType<typeof renderer.createApp>,props:Props,state:State;
let grants:{execute:boolean;plan:boolean;settings:boolean},pageDocument:EventTarget & {hidden:boolean},pageWindow:EventTarget;
const flush=async()=>{for(let i=0;i<25;i++)await Promise.resolve();await nextTick();};
function mount(values:Partial<Props>={}){
  props=reactive({visible:true,source:{reviewId:nextId,entryIds:[1],items:[{entryId:1,name:plan.items[0]!.sourceName,size:10}]},
    initialHistory:false,planRequestId:"",runRequestId:"",persistMarkers:vi.fn(async(value:{plan:string;run:string})=>{props.planRequestId=value.plan;props.runRequestId=value.run;}),...values});
  app=renderer.createApp({setup:()=>()=>h(QuarantineDialog,props)});app.provide(ssrContextKey,{modules:new Set()});app.config.warnHandler=()=>{};app.mount({children:[]});
  state=(app._instance as unknown as{subTree:{component:{setupState:State}}}).subTree.component.setupState;
}
async function executable(){app.unmount();mount({planRequestId:planId});await flush();await state.openForm("move");state.apiToken="synthetic-token";state.firstConfirm=true;state.secondConfirm=true;}

describe("quarantine fixed intent dialog",()=>{
  beforeEach(async()=>{
    vi.useFakeTimers();vi.clearAllMocks();vi.stubGlobal("location",{protocol:"https:"});vi.stubGlobal("crypto",{randomUUID:()=>runId});
    grants=reactive({execute:true,plan:true,settings:true});
    pageDocument=Object.assign(new EventTarget(),{hidden:false});pageWindow=new EventTarget();
    vi.stubGlobal("document",pageDocument);vi.stubGlobal("window",pageWindow);
    mocks.permission.mockReset().mockImplementation(name=>name==="cloud-quarantine:execute"?grants.execute:name==="cloud-quarantine:plan"?grants.plan:name==="settings:write"?grants.settings:true);
    mocks.confirm.mockResolvedValue("confirm");mocks.policy.mockReset().mockResolvedValue(policy);
    mocks.plan.mockReset().mockResolvedValue(plan);mocks.run.mockReset().mockResolvedValue(run);mocks.fetch.mockReset().mockResolvedValue(run);
    mocks.targets.mockReset().mockResolvedValue({items:[{id:9,name:policy.targetName}],networkAccess:false});
    mocks.history.mockReset().mockResolvedValue({items:[],page:1,pageSize:20,total:0,networkAccess:false,writeAvailable:false});
    mocks.prepare.mockReset().mockImplementation(async input=>({...plan,requestId:input.requestId}));mocks.start.mockReset().mockResolvedValue(run);
    mount();await flush();
  });
  afterEach(()=>{app.unmount();vi.useRealTimers();vi.unstubAllGlobals();});
  it("requires explicit target and confirmation and saves UUID before the local plan",async()=>{
    expect(state.targetId).toBeUndefined();expect(state.mayPrepare).toBeFalsy();state.targetId=9;state.localConfirm=true;
    await state.prepare();expect(props.persistMarkers).toHaveBeenCalledExactlyOnceWith({plan:runId,run:""});
    expect(mocks.prepare).toHaveBeenCalledExactlyOnceWith({requestId:runId,reviewId:nextId,entryIds:[1],targetEntryId:9});
    expect(vi.mocked(props.persistMarkers).mock.invocationCallOrder[0]!).toBeLessThan(mocks.prepare.mock.invocationCallOrder[0]!);
  });
  it("consumes confirmations and Token once on an unknown POST and keeps only GET lookup",async()=>{
    await executable();mocks.start.mockRejectedValueOnce(new Error("synthetic-secret-error"));await state.submit();
    expect(props.runRequestId).toBe(runId);expect(state.form).toBeNull();expect(state.apiToken).toBe("");expect(state.firstConfirm).toBe(false);
    await state.submit();await state.openForm("move");state.apiToken="second-token";state.firstConfirm=true;state.secondConfirm=true;
    expect(state.canSubmit).toBe(false);await state.submit();expect(mocks.start).toHaveBeenCalledTimes(1);
    expect(state.errorText).not.toContain("synthetic-secret");await state.loadRun();expect(mocks.run).toHaveBeenCalledExactlyOnceWith(runId);
  });
  it("does not send after route marker failure",async()=>{
    await executable();props.persistMarkers=vi.fn().mockRejectedValueOnce(new Error("navigation"));await flush();await state.submit();
    expect(mocks.start).not.toHaveBeenCalled();expect(state.apiToken).toBe("");expect(state.form).toBeNull();
  });
  it.each(["execute","plan","hidden","pagehide","session"])("invalidates a pending move handoff on %s",async interruption=>{
    await executable();let resume!:()=>void;
    props.persistMarkers=vi.fn(value=>new Promise<void>(done=>{resume=()=>{props.planRequestId=value.plan;props.runRequestId=value.run;done();};}));
    await flush();const pending=state.submit();await flush();
    if(interruption==="execute")grants.execute=false;
    else if(interruption==="plan")grants.plan=false;
    else if(interruption==="hidden"){pageDocument.hidden=true;pageDocument.dispatchEvent(new Event("visibilitychange"));}
    else if(interruption==="session")invalidateSessionRequests();
    else pageWindow.dispatchEvent(new Event("pagehide"));
    await flush();resume();await pending;
    expect(mocks.start).not.toHaveBeenCalled();expect(state.apiToken).toBe("");expect(state.form).toBeNull();
    grants.execute=true;grants.plan=true;pageDocument.hidden=false;pageDocument.dispatchEvent(new Event("visibilitychange"));
    await flush();await state.submit();expect(mocks.start).not.toHaveBeenCalled();
  });
  it("invalidates an observation handoff while its marker is pending",async()=>{
    app.unmount();mocks.run.mockResolvedValue({...run,status:"review",workerSlotHeld:false,pollingRecommended:false});
    mount({planRequestId:planId,runRequestId:runId});await flush();await state.openForm("observe");
    state.apiToken="synthetic-read-token";state.firstConfirm=true;state.secondConfirm=true;
    let resume!:()=>void;props.persistMarkers=vi.fn(value=>new Promise<void>(done=>{resume=()=>{props.planRequestId=value.plan;props.runRequestId=value.run;done();};}));
    // 新观察必须保存不同编号，防止原标记已存在而跳过异步交接。
    vi.stubGlobal("crypto",{randomUUID:()=>nextId});await flush();const pending=state.submit();await flush();
    pageWindow.dispatchEvent(new Event("pagehide"));await flush();resume();await pending;
    expect(mocks.observe).not.toHaveBeenCalled();expect(state.apiToken).toBe("");expect(state.form).toBeNull();
  });
  it("opens an existing request using GET only and bounds polling without closing logs",async()=>{
    app.unmount();mount({planRequestId:planId,runRequestId:runId});await flush();expect(mocks.start).not.toHaveBeenCalled();
    state.logsVisible=true;await vi.advanceTimersByTimeAsync(1500);expect(mocks.run).toHaveBeenCalledTimes(2);expect(state.logsVisible).toBe(true);
    mocks.run.mockResolvedValueOnce({...run,status:"review",pollingRecommended:false,workerSlotHeld:false});await vi.advanceTimersByTimeAsync(1500);
    await vi.advanceTimersByTimeAsync(10000);expect(mocks.run).toHaveBeenCalledTimes(3);
  });
  it("stops polling on a failed GET and never retries a POST",async()=>{
    app.unmount();mount({planRequestId:planId,runRequestId:runId});await flush();mocks.run.mockRejectedValueOnce(new Error("503"));
    await vi.advanceTimersByTimeAsync(1500);await vi.advanceTimersByTimeAsync(15000);expect(mocks.run).toHaveBeenCalledTimes(2);expect(mocks.start).not.toHaveBeenCalled();
  });
  it("clears old history after a failed reload",async()=>{
    state.tab="history";await flush();expect(state.history).not.toBeNull();mocks.history.mockRejectedValueOnce(new Error("503"));await state.loadHistory();
    expect(state.history).toBeNull();expect(state.errorText).toBeTruthy();
  });
  it("loads current execution policy when opening a plan from history",async()=>{
    app.unmount();mount({initialHistory:true});await flush();expect(state.plan).toBeNull();
    await state.choose(planId);await state.openForm("move");state.apiToken="synthetic-token";state.firstConfirm=true;state.secondConfirm=true;
    expect(mocks.policy).toHaveBeenCalled();expect(state.canSubmit).toBe(true);
  });
  it("fences late response on close and erases secrets",async()=>{
    await executable();let resolve!:(v:QuarantineRun)=>void;mocks.start.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
    const pending=state.submit();await flush();state.close();resolve(run);await pending;expect(state.run).toBeNull();expect(state.apiToken).toBe("");expect(state.logsVisible).toBe(false);
  });
  it("blocks HTTP or revoked permission and session changes",async()=>{
    await executable();vi.stubGlobal("location",{protocol:"http:"});app.unmount();mount({planRequestId:planId});await flush();
    await state.openForm("move");state.apiToken="synthetic-token";state.firstConfirm=true;state.secondConfirm=true;expect(state.canSubmit).toBe(false);
    mocks.permission.mockReturnValue(false);expect(state.canSubmit).toBe(false);invalidateSessionRequests();await state.submit();expect(mocks.start).not.toHaveBeenCalled();
  });
  it("does not offer normal local commit after read recovery handoff",async()=>{
    app.unmount();mocks.run.mockResolvedValueOnce({...run,status:"review",workerSlotHeld:false,latestObservationRequestId:nextId,pollingRecommended:false,
      items:[{ordinal:1,state:"verified",reason:null}]});mount({planRequestId:planId,runRequestId:runId});await flush();
    expect(state.mayCommit).toBe(false);await state.commit();expect(mocks.commit).not.toHaveBeenCalled();
  });
});
