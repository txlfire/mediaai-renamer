const LOGIN_PREFERENCE_STORAGE_KEY = "mediaai-login-preferences";

export type LoginPreferences = {
  username: string;
  rememberPassword: boolean;
};

function resolveStorage(storage?: Storage): Storage | undefined {
  if (storage) {
    return storage;
  }
  return typeof globalThis.localStorage === "undefined" ? undefined : globalThis.localStorage;
}

export function loadLoginPreferences(storage?: Storage): LoginPreferences {
  const target = resolveStorage(storage);
  if (!target) {
    return { username: "", rememberPassword: false };
  }
  try {
    const value = JSON.parse(target.getItem(LOGIN_PREFERENCE_STORAGE_KEY) || "null") as Partial<LoginPreferences> | null;
    if (value?.rememberPassword === true && typeof value.username === "string") {
      return { username: value.username.trim(), rememberPassword: true };
    }
  } catch {
    target.removeItem(LOGIN_PREFERENCE_STORAGE_KEY);
  }
  return { username: "", rememberPassword: false };
}

export function saveLoginPreferences(username: string, rememberPassword: boolean, storage?: Storage): void {
  const target = resolveStorage(storage);
  if (!target) {
    return;
  }
  if (!rememberPassword) {
    target.removeItem(LOGIN_PREFERENCE_STORAGE_KEY);
    return;
  }
  target.setItem(LOGIN_PREFERENCE_STORAGE_KEY, JSON.stringify({
    username: username.trim(),
    rememberPassword: true,
  } satisfies LoginPreferences));
}

export function resolvePasswordAutocomplete(rememberPassword: boolean): "current-password" | "off" {
  return rememberPassword ? "current-password" : "off";
}
