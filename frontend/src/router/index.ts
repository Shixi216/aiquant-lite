import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: "/", redirect: "/dashboard" },
    // V2 决策驾驶舱
    {
      path: "/dashboard",
      name: "dashboard",
      meta: { title: "市场总览" },
      component: () => import("../views/DashboardView.vue"),
    },
    {
      path: "/opportunities",
      name: "opportunities",
      meta: { title: "机会排序" },
      component: () => import("../views/OpportunitiesView.vue"),
    },
    {
      path: "/quant",
      name: "quant-lab",
      meta: { title: "量化实验室" },
      component: () => import("../views/QuantLabView.vue"),
    },
    {
      path: "/stocks/:symbol",
      name: "stock-intel",
      meta: { title: "个股研究" },
      component: () => import("../views/StockIntelView.vue"),
    },
    // 旧页面（复盘与实验室分组），路径与组件保持不变
    {
      path: "/sim-tracker",
      meta: { title: "盘中模拟追踪" },
      component: () => import("../views/SimTrackerView.vue"),
    },
    {
      path: "/capital-behavior",
      meta: { title: "资金行为研究" },
      component: () => import("../views/CapitalBehaviorView.vue"),
    },
    {
      path: "/decisions",
      meta: { title: "决策卡片" },
      component: () => import("../views/DecisionsView.vue"),
    },
    {
      path: "/research-health",
      meta: { title: "实验与系统状态" },
      component: () => import("../views/ResearchHealthView.vue"),
    },
    { path: "/:pathMatch(.*)*", redirect: "/dashboard" },
  ],
});

export default router;
