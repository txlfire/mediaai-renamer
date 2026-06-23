/**
 * 前端应用入口。
 *
 * 负责注册 Vue、Pinia、路由和 Element Plus。业务初始化逻辑不放在这里，
 * 后续应按模块拆分到 store 或独立服务中。
 */

import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import "./styles.css";

const app = createApp(App);

// 全局插件统一在入口注册，避免页面组件重复创建基础设施。
app.use(createPinia());
app.use(router);
app.use(ElementPlus);

app.mount("#app");
