<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { searchStocks } from "../api/v2";
import type { StockSearchItem } from "../types/v2";
import { THEME_PRESETS } from "../domain/theme";
import { useThemeStore } from "../stores/theme";

const route = useRoute();
const router = useRouter();
const themeStore = useThemeStore();
const query = ref("");
const searchError = ref("");
const searchResults = ref<StockSearchItem[]>([]);
const searchLoading = ref(false);
const searchFocused = ref(false);
const menuOpen = ref(false);
const pageTitle = computed(() => String(route.meta.title ?? "投研工作台"));
/** 财富管理工作区搜索对象包含 ETF/基金，仅改提示文案，搜索逻辑与股票页共用。 */
const searchPlaceholder = computed(() =>
  route.path.startsWith("/wealth") ? "股票、基金或ETF代码/名称" : "股票代码或中文名称",
);
let searchTimer: ReturnType<typeof setTimeout> | undefined;
let blurTimer: ReturnType<typeof setTimeout> | undefined;
let searchRequest = 0;

/** 一级工作区：侧栏只展示当前工作区的二级导航，避免导航过长。 */
interface NavItem { key: string; to: string; label: string; icon: string }
interface Workspace { id: string; label: string; icon: string; items: NavItem[] }

const workspaces: Workspace[] = [
  {
    id: "equity",
    label: "股票研究",
    icon: "📈",
    items: [
      { key: "dashboard", to: "/dashboard", label: "市场总览", icon: "🧭" },
      { key: "opportunities", to: "/opportunities", label: "机会排序", icon: "🎯" },
      { key: "quant", to: "/quant", label: "量化实验室", icon: "🧪" },
      { key: "stock-intel", to: "/stocks/600519.SH", label: "个股研究", icon: "📊" },
      { key: "sim-tracker", to: "/sim-tracker", label: "盘中模拟追踪", icon: "⏱️" },
      { key: "capital-behavior", to: "/capital-behavior", label: "资金行为研究", icon: "💹" },
      { key: "decisions", to: "/decisions", label: "决策卡片", icon: "🗂️" },
      { key: "research-health", to: "/research-health", label: "实验与系统状态", icon: "🩺" },
    ],
  },
  {
    id: "wealth",
    label: "财富管理",
    icon: "💼",
    items: [
      { key: "wealth-lab", to: "/wealth-lab", label: "大类资产配置", icon: "🏛️" },
      { key: "wealth-factors", to: "/wealth-factors", label: "ETF 多因子研究", icon: "🧮" },
      { key: "wealth-universe", to: "/wealth-universe", label: "多资产研究池", icon: "🌐" },
      { key: "wealth-strategy-factory", to: "/wealth-strategy-factory", label: "策略工厂", icon: "🏭" },
      { key: "wealth-strategy-compare", to: "/wealth-strategy-compare", label: "策略对比与优化", icon: "⚖️" },
      { key: "wealth-ai-risk", to: "/wealth-ai-risk", label: "AI 研究与风控", icon: "🤖" },
    ],
  },
  {
    id: "data",
    label: "数据中心",
    icon: "🗄️",
    items: [
      { key: "task-center", to: "/task-center", label: "数据与研究任务中心", icon: "🗄️" },
    ],
  },
  {
    id: "system",
    label: "系统设置",
    icon: "⚙️",
    items: [
      { key: "settings", to: "/settings", label: "外观与布局", icon: "🎨" },
    ],
  },
  {
    id: "ai",
    label: "AI 研究",
    icon: "🧠",
    items: [
      { key: "ai-research", to: "/ai-research", label: "AI 智能研究工作台", icon: "🧠" },
    ],
  },
];

const activeWorkspace = computed<Workspace>(() => {
  const path = route.path;
  if (path.startsWith("/task-center")) return workspaces[2]!;
  if (path.startsWith("/settings")) return workspaces[3]!;
  if (path.startsWith("/ai-research")) return workspaces[4]!;
  if (path.startsWith("/wealth")) return workspaces[1]!;
  return workspaces[0]!;
});

function switchWorkspace(workspace: Workspace) {
  const target = workspace.items[0];
  if (target) router.push(target.to);
}

function isItemActive(item: NavItem): boolean {
  if (item.key === "stock-intel") return route.name === "stock-intel";
  return route.path === item.to;
}

function focusSearch() {
  if (blurTimer) clearTimeout(blurTimer);
  searchFocused.value = true;
}
function blurSearch() {
  blurTimer = setTimeout(() => { searchFocused.value = false; }, 150);
}

function stockPath(item: StockSearchItem) {
  searchResults.value = [];
  searchFocused.value = false;
  query.value = item.name;
  searchError.value = "";
  router.push(`/stocks/${item.symbol}`);
}

async function loadSearchResults(value: string) {
  if (value !== query.value.trim()) return;
  const request = ++searchRequest;
  searchLoading.value = true;
  try {
    const response = await searchStocks(value);
    if (request === searchRequest) searchResults.value = response.items;
  } catch {
    if (request === searchRequest) searchResults.value = [];
  } finally {
    if (request === searchRequest) searchLoading.value = false;
  }
}

async function searchStock() {
  const value = query.value.trim().toUpperCase();
  const match = /^(\d{6})(?:\.(SH|SZ|BJ))?$/.exec(value);
  if (match) {
    const code = match[1]!;
    const exchange = match[2] ?? (code.startsWith("6") ? "SH" : /^[489]/.test(code) ? "BJ" : "SZ");
    searchError.value = "";
    searchResults.value = [];
    await router.push(`/stocks/${code}.${exchange}`);
    return;
  }
  if (!value) {
    searchError.value = "请输入股票代码或中文名称";
    return;
  }
  await loadSearchResults(query.value.trim());
  if (query.value.trim().toUpperCase() !== value) return;
  const exact = searchResults.value.find((item) => item.name === query.value.trim());
  if (exact) return stockPath(exact);
  if (searchResults.value.length === 1) return stockPath(searchResults.value[0]!);
  searchFocused.value = true;
  searchError.value = searchResults.value.length ? "请选择具体股票" : "未找到匹配股票";
}
watch(query, (value) => {
  ++searchRequest;
  searchLoading.value = false;
  searchResults.value = [];
  searchError.value = "";
  if (searchTimer) clearTimeout(searchTimer);
  const keyword = value.trim();
  if (!keyword || /^(\d{6})(?:\.(SH|SZ|BJ))?$/i.test(keyword)) {
    searchResults.value = [];
    return;
  }
  searchTimer = setTimeout(() => loadSearchResults(keyword), 250);
});
watch(() => route.fullPath, () => {
  ++searchRequest;
  if (searchTimer) clearTimeout(searchTimer);
  searchLoading.value = false;
  searchFocused.value = false;
  menuOpen.value = false;
  searchResults.value = [];
  document.title = `${pageTitle.value} · 怡怡投研`;
}, { immediate: true });
onBeforeUnmount(() => {
  ++searchRequest;
  if (searchTimer) clearTimeout(searchTimer);
  if (blurTimer) clearTimeout(blurTimer);
});
</script>

<template>
  <a class="skip-link" href="#main-content">跳至主要内容</a>
  <aside id="main-navigation" class="sidebar" :class="{ 'is-open': menuOpen }">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">怡</span>
      <div v-if="!themeStore.settings.sidebarCollapsed">怡怡投研<small>看见数据，沉淀判断</small></div>
    </div>
    <div class="workspace-tabs" role="tablist" aria-label="工作区切换">
      <button
        v-for="workspace in workspaces"
        :key="workspace.id"
        type="button"
        role="tab"
        class="workspace-tab"
        :class="{ active: workspace.id === activeWorkspace.id }"
        :aria-selected="workspace.id === activeWorkspace.id"
        :title="workspace.label"
        @click="switchWorkspace(workspace)"
      >
        <span class="ws-icon" aria-hidden="true">{{ workspace.icon }}</span>
        <span v-if="!themeStore.settings.sidebarCollapsed">{{ workspace.label }}</span>
      </button>
    </div>
    <nav aria-label="主导航">
      <RouterLink
        v-for="item in activeWorkspace.items"
        :key="item.key"
        :to="item.to"
        :title="item.label"
        :class="{ 'router-link-active': isItemActive(item) }"
      >
        <span class="nav-icon" aria-hidden="true">{{ item.icon }}</span>
        <span v-if="!themeStore.settings.sidebarCollapsed">{{ item.label }}</span>
      </RouterLink>
    </nav>
    <div v-if="!themeStore.settings.sidebarCollapsed" class="sidebar-footer">
      <span class="status-dot"></span>本地投研工作台<small>行情 · 研究 · 复盘</small>
    </div>
    <button
      v-if="themeStore.settings.navLayout === 'sidebar'"
      class="mini collapse-btn"
      type="button"
      :aria-expanded="!themeStore.settings.sidebarCollapsed"
      @click="themeStore.toggleSidebar()"
    >{{ themeStore.settings.sidebarCollapsed ? "»" : "« 收起" }}</button>
  </aside>
  <main class="main-area">
    <header class="workspace-header">
      <div class="workspace-heading">
        <button class="mini menu-toggle" :aria-expanded="menuOpen" aria-controls="main-navigation" @click="menuOpen = !menuOpen">{{ menuOpen ? '收起导航' : '展开导航' }}</button>
        <div><div class="eyebrow">怡怡投研 / {{ activeWorkspace.label }}</div><h1>{{ pageTitle }}</h1></div>
      </div>
      <div class="header-tools">
        <form class="stock-search" role="search" @submit.prevent="searchStock">
          <label class="sr-only" for="stock-query">股票代码或中文名称</label>
          <input id="stock-query" v-model="query" type="text" :placeholder="searchPlaceholder" autocomplete="off" :aria-invalid="!!searchError" aria-describedby="stock-search-error" aria-controls="stock-search-results" :aria-expanded="searchFocused && searchResults.length > 0" @focus="focusSearch" @blur="blurSearch" />
          <button class="mini" type="submit">研究个股 <span aria-hidden="true">↗</span></button>
          <div v-if="searchFocused && (searchResults.length || searchLoading)" id="stock-search-results" class="search-results" role="listbox" aria-label="股票搜索结果">
            <span v-if="searchLoading && !searchResults.length" class="search-loading">正在搜索…</span>
            <button v-for="item in searchResults" :key="item.symbol" type="button" role="option" @mousedown.prevent @click="stockPath(item)">
              <span><strong>{{ item.name }}</strong><small>{{ item.symbol }}</small></span>
              <em>{{ item.listing_status === 'DELISTED' ? '已退市' : item.company_name || '' }}</em>
            </button>
          </div>
          <span v-if="searchError" id="stock-search-error" class="search-error" role="alert">{{ searchError }}</span>
        </form>
        <details class="theme-menu">
          <summary class="mini" title="主题与外观">🎨<span class="theme-menu-label">主题</span></summary>
          <div class="theme-menu-panel" role="menu" aria-label="快捷主题">
            <div class="theme-menu-row">
              <button
                v-for="preset in THEME_PRESETS"
                :key="preset.id"
                type="button"
                class="theme-preset-dot"
                :class="{ active: themeStore.settings.preset === preset.id }"
                :title="`${preset.name}：${preset.description}`"
                @click.prevent="themeStore.selectPreset(preset.id)"
              >
                <span class="dot-bg" :style="{ background: preset.swatch[0] }"></span>
                <span class="dot-accent" :style="{ background: preset.swatch[1] }"></span>
                <span class="dot-name">{{ preset.name }}</span>
              </button>
            </div>
            <div class="theme-menu-row">
              <div class="seg theme-mode-seg">
                <button type="button" :class="{ active: themeStore.settings.mode === 'light' }" @click="themeStore.update({ mode: 'light' })">浅色</button>
                <button type="button" :class="{ active: themeStore.settings.mode === 'dark' }" @click="themeStore.update({ mode: 'dark' })">深色</button>
              </div>
              <RouterLink class="mini" to="/settings">更多自定义 →</RouterLink>
            </div>
          </div>
        </details>
        <button class="mini mode-toggle" type="button" :title="themeStore.isDark ? '切换到浅色模式' : '切换到深色模式'" @click="themeStore.toggleMode()">{{ themeStore.isDark ? '☀️' : '🌙' }}</button>
      </div>
    </header>
    <div id="main-content" tabindex="-1"><RouterView /></div>
  </main>
</template>

<style scoped>
.nav-group {
  font-size: 11px;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 1px;
  padding: 12px 12px 4px;
}
.sidebar-footer { margin-top: auto; padding: 28px 12px 4px; color: var(--text-dim); font-size: 12px; }
.sidebar-footer small { display: block; padding: 8px 0 0 14px; }
.status-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); margin-right: 8px; }
.brand-mark { display: grid; place-items: center; width: 38px; height: 38px; flex-shrink: 0; background: var(--accent-grad); color: var(--accent-contrast); border-radius: 12px; font-size: 23px; }
.workspace-header { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 28px; padding-bottom: 22px; border-bottom: 1px solid var(--border); }
.workspace-heading { display: flex; align-items: center; gap: 12px; }
.eyebrow { color: var(--text-dim); font-size: 11px; letter-spacing: 1px; margin-bottom: 7px; }
h1 { margin: 0; font-size: 25px; letter-spacing: 1px; }
.header-tools { display: flex; align-items: center; gap: 8px; }
.stock-search { display: flex; gap: 8px; position: relative; }
.stock-search input { width: 240px; padding: 10px 12px; }
.search-results { position: absolute; z-index: 30; top: 44px; left: 0; width: 320px; max-height: 320px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-panel); box-shadow: var(--shadow-pop); }
.search-results button { width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 10px 12px; border: 0; border-bottom: 1px solid var(--border-soft); border-radius: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.search-results button:last-child { border-bottom: 0; }
.search-results button:hover,.search-results button:focus { background: var(--bg-hover); }
.search-results strong,.search-results small { display: block; }
.search-results small,.search-results em,.search-loading { color: var(--text-dim); font-size: 11px; font-style: normal; }
.search-results em { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.search-loading { display: block; padding: 12px; }
.search-error { position: absolute; top: 44px; right: 0; color: var(--warn); font-size: 12px; }
.menu-toggle { display: none; }
/* 主题快捷菜单 */
.theme-menu { position: relative; }
.theme-menu summary { list-style: none; display: inline-flex; align-items: center; gap: 6px; user-select: none; }
.theme-menu summary::-webkit-details-marker { display: none; }
.theme-menu-label { font-size: 12px; }
.theme-menu-panel { position: absolute; right: 0; top: calc(100% + 8px); z-index: 45; width: 268px; padding: 12px; background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius-inner); box-shadow: var(--shadow-pop); display: flex; flex-direction: column; gap: 10px; }
.theme-menu-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; }
.theme-preset-dot { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 7px 6px 5px; border: 1px solid var(--border); border-radius: var(--radius-ctrl); background: var(--bg-panel); cursor: pointer; position: relative; }
.theme-preset-dot.active { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-ring); }
.theme-preset-dot .dot-bg, .theme-preset-dot .dot-accent { width: 22px; height: 22px; border-radius: 7px; border: 1px solid var(--border-soft); }
.theme-preset-dot .dot-accent { margin-top: -14px; width: 12px; height: 12px; border: 2px solid var(--bg-panel); }
.theme-preset-dot .dot-name { font-size: 10.5px; color: var(--text-dim); }
.theme-mode-seg button { padding: 4px 10px; }
/* 侧栏折叠态：只留图标 */
.collapse-btn { margin: 10px 12px 0; }
@media (min-width: 761px) {
  [data-nav-collapse="1"] .sidebar { width: 68px; padding-left: 8px; padding-right: 8px; }
  [data-nav-collapse="1"] .sidebar a { justify-content: center; gap: 0; font-size: 0; }
  [data-nav-collapse="1"] .sidebar a .nav-icon { font-size: 15px; }
  [data-nav-collapse="1"] .workspace-tab { justify-content: center; gap: 0; font-size: 0; }
  [data-nav-collapse="1"] .workspace-tab .ws-icon { font-size: 15px; }
  [data-nav-collapse="1"] .nav-group { visibility: hidden; padding: 4px; }
  [data-nav-collapse="1"] .collapse-btn { margin: 10px 4px 0; text-align: center; }
}
@media (max-width: 760px) {
  .workspace-header { align-items: stretch; flex-direction: column; gap: 16px; }
  .stock-search input { flex: 1; min-width: 0; width: auto; }
  .search-results { top: 44px; width: 100%; }
  .menu-toggle { display: block; }
  .sidebar-footer { display: none; }
  .theme-menu-label { display: none; }
}
</style>
