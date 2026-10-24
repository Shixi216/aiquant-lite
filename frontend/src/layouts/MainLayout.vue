<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { searchStocks } from "../api/v2";
import type { StockSearchItem } from "../types/v2";

const route = useRoute();
const router = useRouter();
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
const navGroups = [
  {
    label: "决策驾驶舱",
    items: [
      { to: "/dashboard", label: "市场总览" },
      { to: "/opportunities", label: "机会排序" },
    ],
  },
  {
    label: "量化研究",
    items: [
      { to: "/quant", label: "量化实验室" },
    ],
  },
  {
    label: "财富管理",
    items: [
      { to: "/wealth-lab", label: "大类资产配置" },
      { to: "/wealth-factors", label: "ETF 多因子研究" },
      { to: "/wealth-universe", label: "多资产研究池" },
      { to: "/wealth-strategy-factory", label: "策略工厂" },
      { to: "/wealth-strategy-compare", label: "策略对比与优化" },
      { to: "/wealth-ai-risk", label: "AI 研究与风控" },
    ],
  },
  {
    label: "个股研究",
    items: [
      { to: "/stocks/600519.SH", label: "个股研究" },
    ],
  },
  {
    label: "复盘与实验室",
    items: [
      { to: "/sim-tracker", label: "盘中模拟追踪" },
      { to: "/capital-behavior", label: "资金行为研究" },
      { to: "/decisions", label: "决策卡片" },
      { to: "/research-health", label: "实验与系统状态" },
    ],
  },
];
</script>

<template>
  <a class="skip-link" href="#main-content">跳至主要内容</a>
  <aside id="main-navigation" class="sidebar" :class="{ 'is-open': menuOpen }">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">怡</span>
      <div>怡怡投研<small>看见数据，沉淀判断</small></div>
    </div>
    <nav aria-label="主导航">
    <template v-for="group in navGroups" :key="group.label">
      <div class="nav-group">{{ group.label }}</div>
      <RouterLink v-for="item in group.items" :key="item.to" :to="item.to" :class="{ 'router-link-active': item.label === '个股研究' && route.name === 'stock-intel' }">
        {{ item.label }}
      </RouterLink>
    </template>
    </nav>
    <div class="sidebar-footer"><span class="status-dot"></span>本地投研工作台<small>行情 · 研究 · 复盘</small></div>
  </aside>
  <main class="main-area">
    <header class="workspace-header">
      <div class="workspace-heading">
        <button class="mini menu-toggle" :aria-expanded="menuOpen" aria-controls="main-navigation" @click="menuOpen = !menuOpen">{{ menuOpen ? '收起导航' : '展开导航' }}</button>
        <div><div class="eyebrow">怡怡投研 / 工作台</div><h1>{{ pageTitle }}</h1></div>
      </div>
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
.brand-mark { display: grid; place-items: center; width: 38px; height: 38px; flex-shrink: 0; background: linear-gradient(145deg, #82c5ff, #467cc0); color: #101a28; border-radius: 12px; font-size: 23px; }
.workspace-header { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 28px; padding-bottom: 22px; border-bottom: 1px solid var(--border); }
.workspace-heading { display: flex; align-items: center; gap: 12px; }
.eyebrow { color: var(--text-dim); font-size: 11px; letter-spacing: 1px; margin-bottom: 7px; }
h1 { margin: 0; font-size: 25px; letter-spacing: 1px; }
.stock-search { display: flex; gap: 8px; position: relative; }
.stock-search input { width: 240px; padding: 10px 12px; }
.search-results { position: absolute; z-index: 30; top: 44px; left: 0; width: 320px; max-height: 320px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-panel); box-shadow: 0 14px 32px rgba(0,0,0,.35); }
.search-results button { width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 10px 12px; border: 0; border-bottom: 1px solid var(--border); border-radius: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.search-results button:last-child { border-bottom: 0; }
.search-results button:hover,.search-results button:focus { background: var(--bg-hover); }
.search-results strong,.search-results small { display: block; }
.search-results small,.search-results em,.search-loading { color: var(--text-dim); font-size: 11px; font-style: normal; }
.search-results em { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.search-loading { display: block; padding: 12px; }
.search-error { position: absolute; top: 44px; right: 0; color: var(--warn); font-size: 12px; }
.menu-toggle { display: none; }
@media (max-width: 760px) {
  .workspace-header { align-items: stretch; flex-direction: column; gap: 16px; }
  .stock-search input { flex: 1; min-width: 0; width: auto; }
  .search-results { top: 44px; width: 100%; }
  .menu-toggle { display: block; }
  .sidebar-footer { display: none; }
}
</style>
