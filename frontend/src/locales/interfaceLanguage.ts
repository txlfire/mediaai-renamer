import { computed, inject, ref, type InjectionKey } from "vue";
import { enUsMessages } from "./en-US";
import { zhCnMessages, type ZhCnMessages } from "./zh-CN";

export type InterfaceLanguage = "zh-CN" | "en-US";

// 只保存当前浏览器的语言标识；没有有效偏好时按浏览器匹配，最终回退简中。
export const interfaceLanguageStorageKey = "mediaai-interface-language";
// 界面支持简中及英语；其他浏览器语言或非法存储值最终回退简中。
const shippedLanguages: readonly InterfaceLanguage[] = ["zh-CN", "en-US"];

const catalogs: Readonly<Record<InterfaceLanguage, ZhCnMessages>> = {
  "zh-CN": zhCnMessages,
  "en-US": { ...zhCnMessages, ...enUsMessages },
};

type LanguagePreferences = {
  enabledLanguages?: readonly InterfaceLanguage[];
  browserLanguages?: readonly string[];
  storage?: () => Pick<Storage, "getItem" | "setItem"> | undefined;
};

function matchBrowserLanguage(value: string): InterfaceLanguage | undefined {
  const primary = value.trim().toLowerCase().split("-")[0];
  if (primary === "zh") return "zh-CN";
  if (primary === "en") return "en-US";
}

export function createInterfaceLanguage(preferences: LanguagePreferences = {}) {
  const enabled = preferences.enabledLanguages ?? shippedLanguages;
  const options = (["zh-CN", "en-US"] as const)
    .filter(language => language === "zh-CN" || enabled.includes(language))
    .map(value => ({ value, label: catalogs[value].app.languageName }));

  const isEnabled = (value: unknown): value is InterfaceLanguage => options.some(option => option.value === value);
  let stored: string | null = null;
  try {
    stored = preferences.storage?.()?.getItem(interfaceLanguageStorageKey) ?? null;
  } catch {
    // 存储受浏览器策略限制时，仍可使用浏览器偏好或简中启动。
  }
  const initial = isEnabled(stored) ? stored
    : preferences.browserLanguages?.map(matchBrowserLanguage).find(isEnabled) ?? "zh-CN";
  const current = ref<InterfaceLanguage>(initial);

  function setLanguage(value: unknown) {
    if (!isEnabled(value)) return;
    current.value = value;
    try {
      preferences.storage?.()?.setItem(interfaceLanguageStorageKey, value);
    } catch {
      // 不因持久化失败撤销本次界面切换。
    }
  }

  return {
    language: computed(() => current.value),
    messages: computed(() => catalogs[current.value]),
    options,
    setLanguage,
  };
}

let defaultInterfaceLanguage = createInterfaceLanguage();
export const interfaceLanguageKey: InjectionKey<ReturnType<typeof createInterfaceLanguage>> = Symbol("interfaceLanguage");

export function initializeInterfaceLanguage(preferences: LanguagePreferences) {
  defaultInterfaceLanguage = createInterfaceLanguage(preferences);
  return defaultInterfaceLanguage;
}

// API、路由守卫及格式化工具在组件外也使用同一界面语言，不访问持久存储或修改业务数据。
export function getInterfaceMessages() {
  return defaultInterfaceLanguage.messages.value;
}

export function getInterfaceLanguage() {
  return defaultInterfaceLanguage.language.value;
}

export function useInterfaceLanguage() {
  return inject(interfaceLanguageKey, defaultInterfaceLanguage);
}
