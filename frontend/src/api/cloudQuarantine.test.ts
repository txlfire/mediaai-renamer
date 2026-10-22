import { afterEach,beforeEach,describe,expect,it,vi } from "vitest";
const mocks=vi.hoisted(()=>({get:vi.fn(),post:vi.fn()}));
vi.mock("./client",()=>({apiClient:mocks}));
import * as api from "./cloudQuarantine";
import { SessionChangedError } from "../utils/sessionGeneration";
import { plan,planId,run,runId } from "../testSupport/cloudQuarantine";

describe("quarantine bounded API",()=>{
  beforeEach(()=>{vi.clearAllMocks();vi.stubGlobal("location",{protocol:"https:"});});
  afterEach(()=>vi.unstubAllGlobals());
  it("keeps original UUID in read-only lookups",async()=>{
    mocks.get.mockResolvedValueOnce({data:plan}).mockResolvedValueOnce({data:run});
    expect(await api.findQuarantinePlan(planId)).toEqual(plan);expect(await api.findQuarantineRun(runId)).toEqual(run);
    expect(mocks.get.mock.calls.map(v=>v[0])).toEqual([`/cloud-drive/quarantine-plan-requests/${planId}`,`/cloud-drive/quarantine-run-requests/${runId}`]);
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("clears input and transport Token references on success or timeout without retry",async()=>{
    for(const fail of [false,true]){
      mocks.post.mockImplementationOnce(async(_url,body)=>{expect(body.apiToken).toBe("synthetic-token");if(fail)throw new Error("secret-provider-response");return{data:run};});
      const input={requestId:runId,expectedRevision:0,policyHash:plan.policyHash,confirmMaintenance:true,confirmGlobalFence:true,apiToken:"synthetic-token"};
      if(fail)await expect(api.startQuarantinePlan(planId,input)).rejects.not.toThrow("secret-provider");
      else expect(await api.startQuarantinePlan(planId,input)).toEqual(run);
      expect(input.apiToken).toBe("");expect(mocks.post.mock.calls[mocks.post.mock.calls.length-1]![1].apiToken).toBe("");
    }
    expect(mocks.post).toHaveBeenCalledTimes(2);
  });
  it("never sends a Token over an HTTP page",async()=>{
    vi.stubGlobal("location",{protocol:"http:"});
    const input={requestId:runId,expectedRevision:0,policyHash:plan.policyHash,confirmMaintenance:true,confirmGlobalFence:true,apiToken:"synthetic-token"};
    await expect(api.startQuarantinePlan(planId,input)).rejects.toThrow("HTTPS");expect(input.apiToken).toBe("");expect(mocks.post).not.toHaveBeenCalled();
  });
  it.each([
    {...run,requestId:planId}, {...run,kind:"observe",events:[{ordinal:1,method:"MoveFile",phase:"responded",responseCode:"OK",receivedBytes:10}]},
    {...run,items:[{ordinal:1,state:"deleted",reason:null}]}, {...run,items:[{ordinal:1,state:"review",reason:null},{ordinal:1,state:"review",reason:null}]},
    {...run,networkAccess:true}, {...run,events:new Array(49).fill({ordinal:1})},
  ])("rejects mismatched or expanded run evidence",async value=>{
    mocks.get.mockResolvedValueOnce({data:value});await expect(api.findQuarantineRun(runId)).rejects.toThrow();
  });
  it("rejects plans that change name or leave the fixed parent",async()=>{
    mocks.get.mockResolvedValueOnce({data:{...plan,items:[{...plan.items[0],targetPath:"/elsewhere/file.txt"}]}});
    await expect(api.findQuarantinePlan(planId)).rejects.toThrow();
  });
  it("propagates session fencing instead of showing a stale error",async()=>{
    const error=new SessionChangedError();mocks.get.mockRejectedValueOnce(error);await expect(api.findQuarantineRun(runId)).rejects.toBe(error);
  });
});
