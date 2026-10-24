<script setup lang="ts">
/**
 * 多资产研究池（v3.1）：资产分类（多维）→ 数据质量（分页）→ 历史可投资性（PIT）。
 * 契约：docs/contracts/wealth-lab-api-v3.yaml + v3.1 后端增量（page/limit/next_page、
 * product_form/underlying/market_scope/currency_risk、pit_rebalance_snapshots）。
 * 所有数量与资格判定均来自后端响应；质量状态筛选契约暂无服务端参数，为前端过滤并明确标注。
 */
import { computed, onMounted, ref } from "vue";
import type { AssetQuality, EligibleData, QualityData, UniversesData, WealthEnvelope } from "../types/wealthLabV3";
import type { QualityStatus } from "../types/wealthLabV3";
import { fetchV3Eligible, fetchV3Quality, fetchV3Universes } from "../api/wealthLabV3";
import type { DataSource } from "../api/wealthLab";
import { errorMessage } from "../api/client";
import { fmtCny } from "../domain/wealthFormat";
import {
  QUALITY_STATUS_CLS,
  QUALITY_STATUS_LABELS,
  backtestEligibilityText,
  buildCategoryStats,
  completeness,
  currencyRiskLabel,
  exchangeLabel,
  marketScopeLabel,
  parseSourceBreakdown,
  productFormLabel,
} from "../domain/wealthV3Format";
import WlDateInput from "../components/wealth/WlDateInput.vue";

const PAGE_SIZE = 10; // 验收口径：每页 10 条，33 只 = 10+10+10+3

/* ---------------- 全量统计（概览/审计/名称映射） ---------------- */

const universesEnvelope = ref<WealthEnvelope<UniversesData> | null>(null);
const allAssets = ref<AssetQuality[]>([]);
const qualityEnvelope = ref<WealthEnvelope<QualityData> | null>(null);
const source = ref<DataSource>("live");
const loading = ref(false);
const error = ref<string | null>(null);

const universes = computed(() => universesEnvelope.value?.data.universes ?? []);
const qualityNotes = computed(() => qualityEnvelope.value?.data.notes ?? []);
const qualityCounts = computed(() => qualityEnvelope.value?.data.quality_counts ?? {});
const offlineMode = computed(() => source.value === "offline-mock");
const categoryStats = computed(() => buildCategoryStats(allAssets.value));

function assetName(symbol: string): string {
  return allAssets.value.find((asset) => asset.symbol === symbol)?.name ?? symbol;
}

/* ---------------- 研究池表格：服务端分页 + 筛选 ---------------- */

const page = ref(1);
const searchText = ref("");
const categoryFilter = ref("all");
const statusFilter = ref<"all" | QualityStatus>("all");
const sortKey = ref<"symbol" | "valid_days" | "completeness" | "avg_amount">("symbol");
const sortAsc = ref(true);

const pageData = ref<WealthEnvelope<QualityData> | null>(null);
const pageLoading = ref(false);
const pageError = ref<string | null>(null);
const pageSource = ref<DataSource>("live");

/** v3.2 起质量状态/关键字有服务端参数（status/q）；离线 Mock 模式仍走前端过滤分页。 */
const statusFilterLocal = computed(() => source.value === "offline-mock" && statusFilter.value !== "all");

const total = computed(() => pageData.value?.data.total ?? 0);
const currentPage = computed(() => pageData.value?.data.page ?? page.value);
const nextPage = computed(() => pageData.value?.data.next_page ?? null);
const pageRangeText = computed(() => {
  if (!total.value) return "—";
  const start = (currentPage.value - 1) * PAGE_SIZE + 1;
  const end = Math.min(currentPage.value * PAGE_SIZE, total.value);
  return `${start}–${end}`;
});

/** 仅离线 Mock 模式走"全量拉取 + 前端过滤分页"；真实后端由 status/q 服务端筛选。 */
const localPaged = computed(() => source.value === "offline-mock");

/** 当前页展示行：全量模式 → 前端过滤+切片；服务端模式 → 后端行 + 当前页排序。 */
const tableRows = computed<AssetQuality[]>(() => {
  let rows = pageData.value?.data.assets ?? [];
  const keyword = searchText.value.trim().toLowerCase();
  if (localPaged.value) {
    if (statusFilterLocal.value) rows = rows.filter((asset) => asset.quality_status === statusFilter.value);
    if (keyword) rows = rows.filter((asset) => asset.symbol.toLowerCase().includes(keyword) || asset.name.toLowerCase().includes(keyword));
  }
  const dir = sortAsc.value ? 1 : -1;
  return [...rows].sort((a, b) => {
    switch (sortKey.value) {
      case "valid_days": return (a.valid_days - b.valid_days) * dir;
      case "completeness": return (a.missing_ratio - b.missing_ratio) * dir;
      case "avg_amount": return (a.avg_amount_20d_cny - b.avg_amount_20d_cny) * dir;
      default: return a.symbol.localeCompare(b.symbol) * dir;
    }
  });
});

/** 前端分页时的总行数（状态筛选后）；服务端分页时用 total。 */
const localFilteredTotal = ref<number | null>(null);
const displayTotal = computed(() => (statusFilterLocal.value ? localFilteredTotal.value ?? 0 : total.value));

const categoryOptions = computed(() => {
  const seen = new Map<string, number>();
  for (const asset of allAssets.value) seen.set(asset.category, (seen.get(asset.category) ?? 0) + 1);
  return [...seen.entries()].map(([value, count]) => ({ value, count }));
});

function toggleSort(key: "symbol" | "valid_days" | "completeness" | "avg_amount"): void {
  if (sortKey.value === key) sortAsc.value = !sortAsc.value;
  else {
    sortKey.value = key;
    sortAsc.value = true;
  }
}

function sortMark(key: string): string {
  if (sortKey.value !== key) return "";
  return sortAsc.value ? " ▲" : " ▼";
}

async function loadPage(target = page.value): Promise<void> {
  const seq = ++pageSeq;
  pageLoading.value = true;
  pageError.value = null;
  try {
    const category = categoryFilter.value === "all" ? undefined : categoryFilter.value;
    // v3.2：status/q 为服务端参数；仅 Mock 模式回退前端过滤分页
    const useLocalPaging = localPaged.value;
    const limit = useLocalPaging ? 200 : PAGE_SIZE;
    const requestPage = useLocalPaging ? 1 : target;
    const statusParam = statusFilter.value === "all" ? undefined : statusFilter.value;
    const response = await fetchV3Quality(category, requestPage, limit, statusParam, searchText.value);
    if (seq !== pageSeq) return;
    pageData.value = response.envelope;
    pageSource.value = response.source;
    if (useLocalPaging) {
      const keyword = searchText.value.trim().toLowerCase();
      let filtered = response.envelope.data.assets;
      if (statusFilter.value !== "all") filtered = filtered.filter((asset) => asset.quality_status === statusFilter.value);
      if (keyword) filtered = filtered.filter((asset) => asset.symbol.toLowerCase().includes(keyword) || asset.name.toLowerCase().includes(keyword));
      localFilteredTotal.value = filtered.length;
      const start = (target - 1) * PAGE_SIZE;
      pageData.value = {
        ...response.envelope,
        data: { ...response.envelope.data, assets: filtered.slice(start, start + PAGE_SIZE), page: target, limit: PAGE_SIZE },
      };
      page.value = target;
    } else {
      page.value = response.envelope.data.page ?? target;
      localFilteredTotal.value = null;
    }
  } catch (err) {
    if (seq === pageSeq) pageError.value = errorMessage(err);
  } finally {
    if (seq === pageSeq) pageLoading.value = false;
  }
}
let pageSeq = 0;

function goPage(delta: number): void {
  const target = currentPage.value + delta;
  if (target < 1) return;
  if (delta > 0 && nextPage.value === null && !statusFilterLocal.value) return;
  if (statusFilterLocal.value) {
    const maxPage = Math.max(1, Math.ceil((localFilteredTotal.value ?? 0) / PAGE_SIZE));
    if (target > maxPage) return;
  }
  void loadPage(target);
}

function onFilterChange(): void {
  page.value = 1; // 切换筛选条件时重置页码
  void loadPage(1);
}

let searchTimer: ReturnType<typeof setTimeout> | undefined;
function onSearchInput(): void {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => onFilterChange(), 350);
}

/* ---------------- 历史可投资资产池（PIT，判定全部来自后端） ---------------- */

const eligibleForm = ref({ universe_id: "multi-asset-v2", asof: "2024-01-04" });
const eligibleEnvelope = ref<WealthEnvelope<EligibleData> | null>(null);
const eligibleLoading = ref(false);
const eligibleError = ref<string | null>(null);
const eligibleSource = ref<DataSource>("live");
let eligibleSeq = 0;

const eligibleOnly = ref<"all" | "eligible" | "excluded">("all");

const eligibilityRows = computed(() => {
  const items = eligibleEnvelope.value?.data.eligibility ?? [];
  if (eligibleOnly.value === "eligible") return items.filter((item) => item.eligible);
  if (eligibleOnly.value === "excluded") return items.filter((item) => !item.eligible);
  return items;
});

async function loadEligible(): Promise<void> {
  const seq = ++eligibleSeq;
  eligibleLoading.value = true;
  eligibleError.value = null;
  try {
    const response = await fetchV3Eligible(eligibleForm.value.universe_id, eligibleForm.value.asof);
    if (seq !== eligibleSeq) return;
    eligibleEnvelope.value = response.envelope;
    eligibleSource.value = response.source;
  } catch (err) {
    if (seq === eligibleSeq) eligibleError.value = errorMessage(err);
  } finally {
    if (seq === eligibleSeq) eligibleLoading.value = false;
  }
}

/* ---------------- 数据来源与质量审计（可展开） ---------------- */

const sourceRows = computed(() => {
  const merged = new Map<string, number>();
  for (const asset of allAssets.value) {
    for (const item of parseSourceBreakdown(asset.source_breakdown)) {
      merged.set(item.source, (merged.get(item.source) ?? 0) + item.rows);
    }
  }
  return [...merged.entries()].map(([sourceKey, rows]) => ({
    source: sourceKey,
    sourceLabel: sourceKey === "OFFICIAL" ? "官方 Tushare" : "代理源（PROXY，仅补充核验）",
    official: sourceKey === "OFFICIAL",
    rows,
  }));
});

const latestUpdate = computed(() =>
  allAssets.value.reduce<string | null>((latest, asset) => (asset.updated_at && (!latest || asset.updated_at > latest) ? asset.updated_at : latest), null),
);

const totalMissingRows = computed(() =>
  allAssets.value.reduce((acc, asset) => acc + Math.round(asset.missing_ratio * asset.expected_days), 0),
);

const adjMissingTotal = computed(() => allAssets.value.reduce((acc, asset) => acc + asset.adj_missing, 0));

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [universesResult, allQuality] = await Promise.all([
      fetchV3Universes(),
      fetchV3Quality(undefined, 1, 200), // 概览统计与审计基于全量
    ]);
    universesEnvelope.value = universesResult.envelope;
    qualityEnvelope.value = allQuality.envelope;
    allAssets.value = allQuality.envelope.data.assets;
    source.value = allQuality.source;
    if (!universes.value.some((u) => u.universe_id === eligibleForm.value.universe_id)) {
      const fallback = universes.value.find((u) => u.universe_id === "multi-asset-v2") ?? universes.value[0];
      if (fallback) eligibleForm.value.universe_id = fallback.universe_id;
    }
    await loadPage(1);
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="universe-view">
    <div class="page-head">
      <p class="page-sub head-sub">中国多资产研究池：多维资产分类 → 数据质量（分页）→ 历史可投资性（PIT 判定全部由后端完成）</p>
      <div class="head-chips">
        <span v-if="offlineMode" class="badge warn">后端未连接 · 契约 Mock 样例</span>
        <span v-else-if="qualityEnvelope?.data.data_mode === 'REAL'" class="badge ok">真实数据</span>
        <span class="badge action">契约 wealth-lab/v3</span>
        <span class="badge">v3.1 分类修正</span>
        <button class="mini" @click="load">{{ loading ? "刷新中…" : "刷新" }}</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>

    <!-- 1. 中国多资产市场概览 -->
    <section class="panel" aria-labelledby="v3-overview-title">
      <h3 id="v3-overview-title">
        中国多资产市场概览
        <span class="h3-extra">统计全部来自接口资产记录（当前共 {{ qualityEnvelope?.data.total ?? allAssets.length }} 只）；未接入类别显示"待接入"</span>
      </h3>
      <div class="cat-grid">
        <div
          v-for="stat in categoryStats"
          :key="stat.key"
          class="cat-card"
          :class="{ pending: !stat.connected }"
        >
          <div class="cat-head">
            <strong>{{ stat.label }}</strong>
            <span :class="stat.connected ? 'badge ok' : 'badge warn'">{{ stat.connected ? "已接入" : "待接入" }}</span>
          </div>
          <template v-if="stat.connected">
            <div class="cat-nums">
              <div><span>已验证资产</span><b>{{ stat.total }}</b></div>
              <div><span>数据合格</span><b class="up">{{ stat.qualified }}</b></div>
              <div><span>数据降级</span><b class="gold-text">{{ stat.degraded }}</b></div>
              <div><span>平均缺失率</span><b>{{ stat.avgMissingRatio === null ? "—" : (stat.avgMissingRatio * 100).toFixed(2) + "%" }}</b></div>
            </div>
            <div class="cat-coverage">覆盖 {{ stat.coverageStart ?? "—" }} ~ {{ stat.coverageEnd ?? "—" }}</div>
          </template>
          <p v-else class="cat-pending">暂无可用数据：该类别尚未接入真实数据池，接入后将显示资产数量与覆盖。</p>
          <p class="cat-risk">主要风险：{{ stat.risk }}</p>
        </div>
      </div>
    </section>

    <!-- 2. 多资产研究池表格（v3.1 分页） -->
    <section class="panel" aria-labelledby="v3-table-title">
      <h3 id="v3-table-title">
        研究资产池（Research Universe）
        <span class="h3-extra">数据合格 {{ qualityCounts.QUALIFIED ?? 0 }} · 降级 {{ qualityCounts.DEGRADED ?? 0 }}（降级多为流动性受限，非采集失败）</span>
      </h3>
      <div class="table-toolbar">
        <input v-model="searchText" type="text" class="search-input" placeholder="搜索代码或名称" aria-label="搜索代码或名称" @input="onSearchInput" />
        <select v-model="categoryFilter" aria-label="按类别筛选" @change="onFilterChange">
          <option value="all">全部类别</option>
          <option v-for="option in categoryOptions" :key="option.value" :value="option.value">
            {{ option.value }}（{{ option.count }}）
          </option>
        </select>
        <select v-model="statusFilter" aria-label="按数据状态筛选" @change="onFilterChange">
          <option value="all">全部质量状态</option>
          <option value="QUALIFIED">数据合格</option>
          <option value="DEGRADED">数据降级</option>
          <option value="REJECTED">质量拒绝</option>
        </select>
        <span class="dim">
          {{ displayTotal ? `第 ${pageRangeText} 条 / 共 ${displayTotal} 只` : "无记录" }}
          <template v-if="statusFilterLocal">· 离线 Mock：筛选为前端过滤</template>
          <template v-else-if="statusFilter !== 'all' || searchText.trim()">· 服务端筛选（status/q）</template>
          <template v-if="pageSource === 'offline-mock'">· Mock 本地分页</template>
        </span>
        <span class="pager">
          <button class="mini" type="button" :disabled="pageLoading || currentPage <= 1" @click="goPage(-1)">上一页</button>
          <span>第 {{ currentPage }} 页</span>
          <button class="mini" type="button" :disabled="pageLoading || (!statusFilterLocal && nextPage === null) || (statusFilterLocal && currentPage >= Math.ceil((localFilteredTotal ?? 0) / PAGE_SIZE))" @click="goPage(1)">下一页</button>
        </span>
      </div>
      <div v-if="pageError" class="error-box">{{ pageError }}</div>
      <div class="table-scroll">
        <table class="data universe-table">
          <thead>
            <tr>
              <th class="sortable" @click="toggleSort('symbol')">资产{{ sortMark("symbol") }}</th>
              <th>类别</th>
              <th>分类维度</th>
              <th>交易所</th>
              <th>上市日期</th>
              <th class="num">数据区间</th>
              <th class="num sortable" @click="toggleSort('valid_days')">有效交易日{{ sortMark("valid_days") }}</th>
              <th class="num sortable" @click="toggleSort('completeness')">完整率{{ sortMark("completeness") }}</th>
              <th class="num sortable" @click="toggleSort('avg_amount')">20日均额{{ sortMark("avg_amount") }}</th>
              <th>质量状态与原因</th>
              <th>回测条件</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="asset in tableRows" :key="asset.symbol">
              <td><strong>{{ asset.name }}</strong><span class="sym-name">{{ asset.symbol }}</span></td>
              <td>{{ asset.category }}</td>
              <td>
                <div class="dim-chips">
                  <span v-if="asset.product_form" class="dim-chip">{{ productFormLabel(asset.product_form) }}</span>
                  <span v-if="asset.market_scope" class="dim-chip">{{ marketScopeLabel(asset.market_scope) }}</span>
                  <span v-if="asset.currency_risk" class="dim-chip warn-chip">汇率：{{ currencyRiskLabel(asset.currency_risk) }}</span>
                  <span v-if="!asset.product_form && !asset.market_scope" class="dim">—</span>
                </div>
              </td>
              <td>{{ exchangeLabel(asset.exchange) }}</td>
              <td>{{ asset.list_date ?? "—" }}<span v-if="asset.delist_date" class="sym-name">（{{ asset.delist_date }} 退市）</span></td>
              <td class="num">{{ asset.data_first ?? "—" }} ~ {{ asset.data_last ?? "—" }}</td>
              <td class="num">{{ asset.valid_days.toLocaleString("zh-CN") }} / {{ asset.expected_days.toLocaleString("zh-CN") }}</td>
              <td class="num">{{ completeness(asset.missing_ratio) }}</td>
              <td class="num">{{ fmtCny(asset.avg_amount_20d_cny) }}</td>
              <td class="reason-cell">
                <span :class="QUALITY_STATUS_CLS[asset.quality_status]">{{ QUALITY_STATUS_LABELS[asset.quality_status] }}</span>
                <p v-if="asset.notes" class="notes-line">{{ asset.notes }}</p>
                <p v-else-if="asset.quality_status === 'DEGRADED'" class="notes-line dim">—</p>
              </td>
              <td :class="backtestEligibilityText(asset.quality_status).cls">{{ backtestEligibilityText(asset.quality_status).text }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!tableRows.length && !pageLoading" class="empty">没有符合筛选条件的资产。</p>
      <p class="dim page-note">排序在当前页内生效（服务端排序待接入）；筛选（状态/关键字/类别）由服务端执行；官方与代理来源明细见下方技术审计。</p>
    </section>

    <!-- 3. 历史可投资资产池 -->
    <section class="panel" aria-labelledby="v3-eligible-title">
      <h3 id="v3-eligible-title">
        历史可投资资产池（Backtest Eligible · PIT）
        <span class="h3-extra">研究资产池 = 全部已验证资产；历史可投资池 = 指定历史决策日满足上市/覆盖/流动性/策略约束的资产</span>
      </h3>
      <div class="eligible-form">
        <label class="field">
          <span>研究资产池（含 v3.1 修正版本）</span>
          <select v-model="eligibleForm.universe_id">
            <option v-for="u in universes" :key="u.universe_id" :value="u.universe_id">
              {{ u.name }} · v{{ u.universe_version }}（{{ u.members }} 只）
            </option>
          </select>
        </label>
        <WlDateInput v-model="eligibleForm.asof" label="历史决策日期" />
        <button class="mini primary" type="button" :disabled="eligibleLoading" @click="loadEligible">{{ eligibleLoading ? "判定中…" : "查询历史可投资性" }}</button>
        <label class="field">
          <span>显示范围</span>
          <select v-model="eligibleOnly">
            <option value="all">全部（{{ eligibleEnvelope?.data.total ?? "—" }}）</option>
            <option value="eligible">可投资（{{ eligibleEnvelope?.data.eligible_count ?? "—" }}）</option>
            <option value="excluded">不符合条件</option>
          </select>
        </label>
      </div>
      <div v-if="eligibleError" class="error-box">{{ eligibleError }}</div>

      <template v-if="eligibleEnvelope">
        <div class="pit-strip">
          <span><b>决策时点</b>{{ eligibleEnvelope.data.asof }}</span>
          <span><b>可投资</b><em class="up">{{ eligibleEnvelope.data.eligible_count }}</em> / {{ eligibleEnvelope.data.total }} 只</span>
          <span class="dim">⚠ {{ eligibleEnvelope.data.survivorship_note }}</span>
        </div>
        <div class="table-scroll">
          <table class="data">
            <thead>
              <tr><th>资产</th><th>判定结果</th><th class="num">截至当日历史天数</th><th>不符合原因（后端判定）</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in eligibilityRows" :key="item.symbol">
                <td><strong>{{ assetName(item.symbol) }}</strong><span class="sym-name">{{ item.symbol }}</span></td>
                <td>
                  <span :class="item.eligible ? 'badge ok' : 'badge warn'">{{ item.eligible ? "可投资" : "不可投资" }}</span>
                </td>
                <td class="num">{{ item.history_days_at_asof.toLocaleString("zh-CN") }}</td>
                <td class="dim-cell">
                  <template v-if="item.reasons.length">
                    <span v-for="reason in item.reasons" :key="reason" class="reason-chip">{{ reason }}</span>
                  </template>
                  <template v-else>—</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <div v-else-if="!eligibleLoading" class="empty">选择资产池与历史决策日期后查询。</div>
    </section>

    <!-- 4. 数据来源与质量审计 -->
    <section class="panel" aria-labelledby="v3-audit-title">
      <h3 id="v3-audit-title">
        数据来源与质量审计
        <span class="h3-extra">官方与代理来源严格区分；前端不读取 Token、不直连数据供应商</span>
      </h3>
      <details class="audit">
        <summary>展开技术审计：数据供应商 / 采集时间 / 覆盖与缺失 / 质量说明</summary>
        <div class="audit-grid">
          <div class="r-block">
            <h4>数据供应商与调用规模</h4>
            <div class="w-chips">
              <span v-for="row in sourceRows" :key="row.source" class="w-chip" :class="{ proxy: !row.official }">
                {{ row.sourceLabel }}（{{ row.source }}）：{{ row.rows.toLocaleString("zh-CN") }} 行
              </span>
            </div>
            <p class="dim-line">etf_basic / etf_share_size 无官方权限，仅以代理源补充核验并明确标注 PROXY，不冒充官方。</p>
          </div>
          <div class="r-block">
            <h4>覆盖与缺失</h4>
            <p class="dim-line">最近采集时间：{{ latestUpdate ?? "—" }}</p>
            <p class="dim-line">缺失记录合计：{{ totalMissingRows.toLocaleString("zh-CN") }} 行（分母为池内真实交易日历）</p>
            <p class="dim-line">复权因子缺失：{{ adjMissingTotal.toLocaleString("zh-CN") }} 条（契约约定不填充）</p>
          </div>
          <div class="r-block">
            <h4>数据质量说明（接口返回）</h4>
            <p v-for="note in qualityNotes" :key="note" class="dim-line">· {{ note }}</p>
          </div>
        </div>
      </details>
    </section>

    <p class="research-note">research_only · PIT 判定与幸存者偏差声明均以后端响应为准 · 前端不推断、不填充、不伪造</p>
  </div>
</template>

<style scoped>
.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.head-sub { margin: 0; font-size: 13px; }
.head-chips { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.cat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.cat-card { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; background: var(--bg-hover); display: flex; flex-direction: column; gap: 8px; }
.cat-card.pending { opacity: 0.75; border-style: dashed; }
.cat-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.cat-head strong { font-size: 13.5px; }
.cat-nums { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px 10px; }
.cat-nums div { display: flex; flex-direction: column; gap: 1px; }
.cat-nums span { font-size: 11px; color: var(--text-dim); }
.cat-nums b { font-size: 17px; font-variant-numeric: tabular-nums; }
.cat-coverage { font-size: 11.5px; color: var(--text-dim); font-variant-numeric: tabular-nums; }
.cat-pending { margin: 0; font-size: 12px; color: var(--text-dim); line-height: 1.6; }
.cat-risk { margin: 0; font-size: 11.5px; color: var(--text-dim); border-top: 1px solid var(--border-soft); padding-top: 7px; }
.table-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.search-input { min-width: 200px; }
.dim { color: var(--text-dim); font-size: 12px; }
.pager { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; font-size: 12.5px; color: var(--text-dim); font-variant-numeric: tabular-nums; }
.universe-table th, .universe-table td { font-size: 13px; padding: 10px 11px; vertical-align: top; }
.sortable { cursor: pointer; user-select: none; }
.sortable:hover { color: var(--accent); }
.dim-chips { display: flex; flex-direction: column; gap: 3px; }
.dim-chip { display: inline-block; width: fit-content; padding: 1px 8px; border: 1px solid var(--border); border-radius: 8px; font-size: 11px; color: var(--text); background: var(--bg-hover); }
.dim-chip.warn-chip { border-color: color-mix(in srgb, var(--warn) 40%, transparent); color: var(--warn); }
.reason-cell { max-width: 260px; }
.notes-line { margin: 5px 0 0; font-size: 11.5px; color: var(--text-dim); line-height: 1.55; }
.page-note { margin: 10px 0 0; font-size: 11.5px; color: var(--text-dim); }
.eligible-form { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; color: var(--text-dim); }
.field select { min-width: 220px; }
button.primary { border-color: var(--accent); color: var(--accent); padding: 8px 16px; }
button.primary:hover:not(:disabled) { background: var(--accent-soft); }
.pit-strip {
  display: flex; flex-wrap: wrap; align-items: center; gap: 18px;
  margin-bottom: 12px; padding: 10px 14px;
  border: 1px solid var(--accent-ring); border-radius: 10px;
  background: var(--accent-softer); font-size: 13px; font-variant-numeric: tabular-nums;
}
.pit-strip b { color: var(--text-dim); font-weight: 600; margin-right: 6px; }
.pit-strip em { font-style: normal; font-size: 16px; font-weight: 700; }
.pit-strip .dim { font-size: 12px; }
.reason-chip { display: inline-block; margin: 2px 6px 2px 0; padding: 2px 9px; border: 1px solid color-mix(in srgb, var(--warn) 40%, transparent); border-radius: 9px; font-size: 11.5px; color: var(--warn); }
.audit { margin-top: 4px; }
.audit summary { cursor: pointer; color: var(--text-dim); font-size: 13px; user-select: none; padding: 6px 0; }
.audit summary:hover { color: var(--accent); }
.audit-grid { display: grid; grid-template-columns: 1.2fr 1fr 1fr; gap: 12px; margin-top: 12px; }
.r-block { border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; background: var(--bg-hover); }
.r-block h4 { margin: 0 0 8px; font-size: 12.5px; color: var(--text-dim); font-weight: 600; }
.w-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.w-chip { display: inline-block; padding: 3px 10px; border: 1px solid color-mix(in srgb, var(--down) 35%, transparent); border-radius: 9px; font-size: 12px; color: var(--down); font-variant-numeric: tabular-nums; }
.w-chip.proxy { border-color: color-mix(in srgb, var(--warn) 45%, transparent); color: var(--warn); }
.dim-line { font-size: 12.5px; color: var(--text-dim); margin: 6px 0 0; line-height: 1.65; }
.research-note { margin: 6px 0 0; font-size: 12px; color: var(--text-dim); text-align: center; }
@media (max-width: 1280px) {
  .cat-grid { grid-template-columns: repeat(2, 1fr); }
  .audit-grid { grid-template-columns: 1fr; }
}
</style>
