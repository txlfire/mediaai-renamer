import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import { zhCnMessages } from "./locales/zh-CN";
import router from "./router";
import "./styles.css";

const app = createApp(App);
const appLocale = {
  ...zhCn,
  el: {
    ...zhCn.el,
    table: {
      ...zhCn.el.table,
      emptyText: zhCnMessages.common.emptyTable,
    },
  },
};

app.use(createPinia());
app.use(router);
app.use(ElementPlus, { locale: appLocale });

app.mount("#app");
