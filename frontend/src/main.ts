import "element-plus/dist/index.css";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import { zhCnMessages } from "./locales/zh-CN";
import router from "./router";
import { installElementPlus } from "./plugins/elementPlus";
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
installElementPlus(app, { locale: appLocale });

app.mount("#app");
