import "element-plus/dist/index.css";
import en from "element-plus/es/locale/lang/en";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { createPinia } from "pinia";
import { computed, createApp, watch } from "vue";

import App from "./App.vue";
import { initializeInterfaceLanguage, interfaceLanguageKey } from "./locales/interfaceLanguage";
import router from "./router";
import { installElementPlus } from "./plugins/elementPlus";
import "./styles.css";

const app = createApp(App);
const interfaceLanguage = initializeInterfaceLanguage({
  browserLanguages: navigator.languages.length ? navigator.languages : [navigator.language],
  storage: () => window.localStorage,
});
app.provide(interfaceLanguageKey, interfaceLanguage);
const stopLang = watch(interfaceLanguage.language, language => {
  document.documentElement.lang = language;
}, { immediate: true });
app.onUnmount(stopLang);

const elementConfig = computed(() => {
  const locale = interfaceLanguage.language.value === "en-US" ? en : zhCn;
  return {
    locale: {
      ...locale,
      el: {
        ...locale.el,
        table: { ...locale.el.table, emptyText: interfaceLanguage.messages.value.common.emptyTable },
      },
    },
  };
});

app.use(createPinia());
app.use(router);
installElementPlus(app, elementConfig);

app.mount("#app");
