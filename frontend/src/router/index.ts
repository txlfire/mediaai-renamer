/**
 * 前端路由配置。
 *
 * M1 阶段提供媒体源、扫描任务和扫描结果三个操作台页面。
 */

import { createRouter, createWebHistory } from "vue-router";

import MediaSourcesView from "../views/MediaSourcesView.vue";
import ScanJobsView from "../views/ScanJobsView.vue";
import ScanResultsView from "../views/ScanResultsView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      redirect: "/media-sources",
    },
    {
      path: "/media-sources",
      name: "media-sources",
      component: MediaSourcesView,
    },
    {
      path: "/scan-jobs",
      name: "scan-jobs",
      component: ScanJobsView,
    },
    {
      path: "/scan-results",
      name: "scan-results",
      component: ScanResultsView,
    },
  ],
});

export default router;
