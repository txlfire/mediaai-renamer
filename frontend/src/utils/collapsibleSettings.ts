export function resolveStoredCollapsedState(
  storedValue: string | null,
  defaultCollapsed: boolean,
) {
  if (storedValue === "true") {
    return true;
  }
  if (storedValue === "false") {
    return false;
  }
  return defaultCollapsed;
}
