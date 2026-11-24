import { readonly, shallowRef } from "vue";
import { CloudConnectionRequestError, type CloudConnectionChoice, type CloudDirectoryChoice } from "../api/cloudConnections";
import type { BoundCloudSource } from "../api/cloudBoundInventory";
import type { CloudSourceRevisions } from "../api/cloudDiagnostics";
import { getSessionGeneration } from "./sessionGeneration";

export type CloudSourceTicket = Readonly<{
  source: Readonly<BoundCloudSource>;
  revisions: Readonly<CloudSourceRevisions>;
}>;

type Draft = Readonly<{
  connection: Readonly<CloudConnectionChoice> | null;
  directory: Readonly<CloudDirectoryChoice> | null;
  selectedPath: string;
}>;

const emptyDraft = (): Draft => ({ connection: null, directory: null, selectedPath: "" });
const uuid = (value: string) => /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
const revision = (value: number) => Number.isSafeInteger(value) && value >= 0;

// 只管理新任务草稿的来源；原任务始终按自己的context/UUID查询，不随本草稿变化。
export function createCloudSourceDraft(onInvalidate: () => void) {
  const draft = shallowRef<Draft>(emptyDraft());
  const ownerSession = getSessionGeneration();
  let active = true;
  let disposed = false;
  let ticket: CloudSourceTicket | null = null;

  function invalidate() {
    ticket = null;
    onInvalidate();
  }

  function sessionValid() {
    if (getSessionGeneration() === ownerSession && !disposed) return true;
    if (!disposed) {
      disposed = true;
      active = false;
      draft.value = emptyDraft();
      invalidate();
    }
    return false;
  }

  function requireActive() {
    if (!sessionValid() || !active) {
      throw new CloudConnectionRequestError("authentication_required", null, "not_started", false);
    }
  }

  function invalidScope(): never {
    throw new CloudConnectionRequestError("invalid_scope", null, "not_started", false);
  }

  function selectConnection(choice: CloudConnectionChoice | null) {
    requireActive();
    // 先废止旧交接，即使新选择非法，也不能继续使用旧政策或Token。
    draft.value = emptyDraft();
    invalidate();
    if (!choice) return;
    if (!uuid(choice.id) || !["enabled", "disabled"].includes(choice.state)
        || ![choice.securityRevision, choice.stateRevision, choice.grantRevision].every(revision)) invalidScope();
    const connection = Object.freeze({ id: choice.id, name: choice.name, state: choice.state,
      securityRevision: choice.securityRevision, stateRevision: choice.stateRevision, grantRevision: choice.grantRevision });
    draft.value = { connection, directory: null, selectedPath: "" };
  }

  function selectDirectory(choice: CloudDirectoryChoice | null) {
    requireActive();
    const connection = draft.value.connection;
    draft.value = { connection, directory: null, selectedPath: "" };
    invalidate();
    if (!choice) return;
    if (!connection || choice.connectionId !== connection.id || !uuid(choice.id)
        || !["enabled", "disabled"].includes(choice.state)
        || ![choice.securityRevision, choice.stateRevision, choice.directoryGrantRevision].every(revision)) invalidScope();
    // 选择来自授权列表，不读取全局默认值；服务端仍须再次核对所有修订与权限。
    const directory = Object.freeze({ id: choice.id, connectionId: choice.connectionId, name: choice.name, state: choice.state,
      metadataRevision: choice.metadataRevision, securityRevision: choice.securityRevision, stateRevision: choice.stateRevision,
      approvedRoot: choice.approvedRoot, mappingKey: choice.mappingKey, quarantineFolderName: choice.quarantineFolderName,
      webExecutionEnabled: choice.webExecutionEnabled, directoryRenameEnabled: choice.directoryRenameEnabled,
      quarantineEnabled: choice.quarantineEnabled, directoryGrantRevision: choice.directoryGrantRevision,
      networkAccess: false as const, cloudExecutionAvailable: false as const });
    draft.value = { connection, directory, selectedPath: choice.approvedRoot };
  }

  function selectPath(path: string) {
    requireActive();
    if (path === draft.value.selectedPath) return;
    draft.value = { ...draft.value, selectedPath: path };
    invalidate();
  }

  function capture(): CloudSourceTicket {
    requireActive();
    const { connection, directory, selectedPath } = draft.value;
    if (!connection || !directory || !selectedPath || directory.connectionId !== connection.id) invalidScope();
    // 根边界按路径段核对；完整编码/字符规则及当前授权仍由policy/prepare执行。
    if (!(selectedPath === directory.approvedRoot || selectedPath.startsWith(directory.approvedRoot + "/"))) invalidScope();
    ticket = Object.freeze({ source: Object.freeze({ connectionId: connection.id, directoryProfileId: directory.id, selectedPath }),
      revisions: Object.freeze({ connectionSecurityRevision: connection.securityRevision,
        connectionStateRevision: connection.stateRevision, grantRevision: connection.grantRevision,
        directorySecurityRevision: directory.securityRevision, directoryStateRevision: directory.stateRevision,
        directoryGrantRevision: directory.directoryGrantRevision }) });
    return ticket;
  }

  function isCurrent(value: CloudSourceTicket): boolean {
    return sessionValid() && active && ticket !== null && ticket === value;
  }

  function suspend() {
    active = false;
    invalidate();
  }

  function resume(): boolean {
    if (!sessionValid()) return false;
    active = true;
    return true;
  }

  function dispose() {
    disposed = true;
    active = false;
    draft.value = emptyDraft();
    invalidate();
  }

  return { draft: readonly(draft), selectConnection, selectDirectory, selectPath, capture, isCurrent,
    invalidate, suspend, resume, dispose };
}
