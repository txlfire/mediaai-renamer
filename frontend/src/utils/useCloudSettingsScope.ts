import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useAuthStore } from "../stores/auth";
import { getSessionGeneration, isSessionChangedError } from "./sessionGeneration";
import { getInterfaceMessages } from "../locales/interfaceLanguage";

export function useCloudSettingsScope(reset: () => void, load: () => Promise<void>) {
  const auth = useAuthStore();
  const allowed = computed(() => auth.hasPermission("settings:write"));
  const busy = ref(false), error = ref("");
  let version = 0, active = true;
  function begin() {
    if (!active || !allowed.value || busy.value || (typeof document !== "undefined" && document.hidden)) return null;
    busy.value = true; error.value = "";
    return { version: ++version, session: getSessionGeneration(), userId: auth.currentUser?.id };
  }
  function current(ticket: ReturnType<typeof begin>) {
    return ticket !== null && active && allowed.value && ticket.version === version
      && ticket.session === getSessionGeneration() && ticket.userId === auth.currentUser?.id;
  }
  function fail(ticket: ReturnType<typeof begin>, cause: unknown) {
    if (current(ticket) && cause !== "cancel" && cause !== "close" && !isSessionChangedError(cause)) {
      error.value = getInterfaceMessages().cloudDriveSettings.failed;
      // 错误代码和文案都按本地白名单映射，不展示任意异常原文或请求载荷。
      const reason = (cause as { reasonCode?: unknown } | null)?.reasonCode;
      if (typeof reason === "string") {
        const reasons = getInterfaceMessages().cloudConnections.reasons;
        if (Object.prototype.hasOwnProperty.call(reasons, reason)) error.value = reasons[reason as keyof typeof reasons];
        else if (reason === "revision_conflict" || reason === "request_context_conflict") error.value = getInterfaceMessages().cloudDriveSettings.conflict;
        else if (reason === "invalid_fields" || reason === "invalid_scope") error.value = getInterfaceMessages().cloudDriveSettings.invalid;
        else if (reason === "storage_not_migrated") error.value = getInterfaceMessages().cloudDriveSettings.storageNotMigrated;
      }
    }
  }
  function finish(ticket: ReturnType<typeof begin>) { if (current(ticket)) busy.value = false; }
  function invalidate() { version++; busy.value = false; error.value = ""; reset(); }
  function visibility() { if (document.hidden) invalidate(); }
  watch(() => [allowed.value, auth.currentUser?.id], () => {
    invalidate(); if (allowed.value) void load();
  });
  onMounted(() => { if (typeof document !== "undefined") document.addEventListener("visibilitychange", visibility); void load(); });
  onBeforeUnmount(() => { active = false; invalidate(); if (typeof document !== "undefined") document.removeEventListener("visibilitychange", visibility); });
  return { allowed, busy, error, begin, current, fail, finish };
}
