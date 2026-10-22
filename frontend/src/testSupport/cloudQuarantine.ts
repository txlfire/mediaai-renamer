import type { QuarantinePlan, QuarantinePolicy, QuarantineRun } from "../api/cloudQuarantine";

export const planId="12345678-1234-1234-1234-123456789abc";
export const runId="22345678-1234-1234-1234-123456789abc";
export const nextId="32345678-1234-1234-1234-123456789abc";
export const policy:QuarantinePolicy={planAvailable:true,reason:null,policyHash:"a".repeat(64),executionEnabled:true,
  executionImplemented:true,executionAvailable:true,executionReason:null,canExecute:true,targetName:"_MediaAI_Quarantine",
  controlledMode:true,atomicIdentityGuaranteed:false,limits:{items:5,seconds:1800,executionSeconds:180,baseRpc:3,rpcPerItem:9,bytes:1048576,observeSeconds:90},networkAccess:false,writeAvailable:false};
export const plan:QuarantinePlan={id:planId,requestId:planId,reviewId:nextId,inventoryJobId:nextId,kind:"forward",revision:0,
  status:"prepared",valid:true,invalidReason:null,policyHash:policy.policyHash,snapshotHash:"b".repeat(64),parentPath:"/cloud/category",
  targetName:policy.targetName,createdAt:1792300000,expiresAt:1792301800,executionRequestId:null,networkAccess:false,writeAvailable:false,
  items:[{entryId:1,generation:1,remoteId:"synthetic-file-1",sourceName:"promo-resource.txt",sourcePath:"/cloud/category/promo-resource.txt",
    targetPath:"/cloud/category/_MediaAI_Quarantine/promo-resource.txt",size:10,reason:"Synthetic manual review",evidence:["promotional_phrase"]}]};
export const run:QuarantineRun={id:runId,requestId:runId,planId,kind:"move",originalRunId:null,latestObservationRequestId:null,
  status:"running",reason:null,revision:1,cancelRequested:false,globalSlotHeld:true,workerSlotHeld:true,pollingRecommended:true,
  usedRpc:0,maxRpc:12,receivedBytes:0,maxBytes:1048576,createdAt:1792300000,expiresAt:1792300180,logId:1,logAvailable:true,
  committedEpoch:null,positions:[],items:[],events:[],networkAccess:false,replayed:false};
