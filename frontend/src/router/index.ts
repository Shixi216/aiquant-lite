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
    // 财富管理实验室（大类资产配置，契约 wealth-lab/v1）
    {
      path: "/wealth-lab",
      name: "wealth-lab",
      meta: { title: "大类资产配置" },
      component: () => import("../views/WealthLabView.vue"),
    },
    // 财富实验室 v2 扩展工作区（契约 wealth-lab-api-v2.yaml）
    {
      path: "/wealth-factors",
      name: "wealth-factors",
      meta: { title: "ETF 多因子研究" },
      component: () => import("../views/WealthFactorsView.vue"),
    },
    // 多资产研究池（v3 契约）
    {
      path: "/wealth-universe",
      name: "wealth-universe",
      meta: { title: "多资产研究池" },
      component: () => import("../views/WealthUniverseView.vue"),
    },
    {
      path: "/wealth-strategy-factory",
      name: "wealth-strategy-factory",
      meta: { title: "策略工厂" },
      component: () => import("../views/WealthStrategyFactoryView.vue"),
    },
    {
      path: "/wealth-strategy-compare",
      name: "wealth-strategy-compare",
      meta: { title: "策略对比与优化" },
      component: () => import("../views/WealthStrategyCompareView.vue"),
    },
    {
      path: "/wealth-ai-risk",
      name: "wealth-ai-risk",
      meta: { title: "AI 研究与风控" },
      component: () => import("../views/WealthAiRiskView.vue"),
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
    // 数据与研究任务中心（阶段二：按钮→真实后端控制 API）
    {
      path: "/task-center",
      name: "task-center",
      meta: { title: "数据与研究任务中心" },
      component: () => import("../views/TaskCenterView.vue"),
    },
    // 系统设置：主题与布局定制中心
    {
      path: "/settings",
      name: "settings",
      meta: { title: "外观与布局设置" },
      component: () => import("../views/SettingsView.vue"),
    },
    { path: "/:pathMatch(.*)*", redirect: "/dashboard" },
  ],
});

export default router;
