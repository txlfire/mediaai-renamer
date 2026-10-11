import type { Pinia } from "pinia";

type ResettableStore = {
  $id: string;
  $reset: () => void;
  [key: string]: unknown;
};

type PiniaWithStores = Pinia & {
  _s: Map<string, ResettableStore>;
};

const WORKSPACE_STORE_IDS = [
  "media",
  "preview",
  "pendingFiles",
  "renameOperation",
  "pagination",
  "tableSort",
  "settings",
];

export function resetWorkspaceState(pinia: Pinia): void {
  const stores = (pinia as PiniaWithStores)._s;
  const pendingStore = stores.get("pendingFiles");
  const renameStore = stores.get("renameOperation");

  if (typeof pendingStore?.stopMovePolling === "function") {
    pendingStore.stopMovePolling();
  }
  if (typeof renameStore?.stopExecutionPolling === "function") {
    renameStore.stopExecutionPolling();
  }

  const moveVersion = Number(pendingStore?.moveVersion ?? 0);
  const executionVersion = Number(renameStore?.executionVersion ?? 0);
  const rollbackVersion = Number(renameStore?.rollbackVersion ?? 0);

  WORKSPACE_STORE_IDS.forEach((storeId) => stores.get(storeId)?.$reset());

  if (pendingStore) {
    pendingStore.moveVersion = moveVersion;
  }
  if (renameStore) {
    renameStore.executionVersion = executionVersion;
    renameStore.rollbackVersion = rollbackVersion;
  }
}
