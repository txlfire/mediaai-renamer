/**
 * 前端路由配置。
 *
 * 当前 M0 阶段只提供首页，后续扫描、日志、配置等页面应在这里集中注册。
 */

import { createRouter, createWebHistory } from "vue-router";

import HomeView from "../views/HomeView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "home",
      component: HomeView,
    },
  ],
});

export default router;
