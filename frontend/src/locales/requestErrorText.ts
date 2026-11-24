import { getInterfaceLanguage, getInterfaceMessages } from "./interfaceLanguage";

export function localizedRequestError(error: unknown): string | undefined {
  // 中文保留现有调用方依赖的详情。英语仅解释已知状态，不猜测远端原文，也不声称操作一定未发生。
  if (getInterfaceLanguage() !== "en-US") return;
  const request = error as { code?: string; config?: { url?: string }; response?: { status?: number } } | null;
  const status = request?.response?.status;
  const { auth, requestErrors: text } = getInterfaceMessages();
  if (status === 400 || status === 422) return text.invalid;
  if (status === 401) return request?.config?.url?.split("?")[0]?.endsWith("/auth/login") ? auth.loginFailed : auth.sessionExpired;
  if (status === 403) return auth.permissionDenied;
  if (status === 404) return text.missing;
  if (status === 409) return text.conflict;
  if (status === 429) return text.throttled;
  if (typeof status === "number" && status >= 500) return text.unavailable;
  if (request?.code === "ECONNABORTED" || request?.code === "ETIMEDOUT") return text.timeout;
  if (request?.code === "ERR_NETWORK") return text.network;
  return text.failed;
}
