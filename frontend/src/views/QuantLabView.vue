<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  fetchQuantBreadth,
  fetchQuantDragonTiger,
  fetchQuantLatestSession,
  fetchQuantLimitPools,
  fetchQuantMargin,
  fetchQuantOverview,
  fetchQuantRps,
  fetchQuantScan,
} from "../api/v2";
import { errorMessage } from "../api/client";
import { usePolling } from "../composables/usePolling";
import type {
  BreadthPoint,
  DragonTigerItem,
  LatestSessionData,
  LimitPoolsData,
  MarginPoint,
  QuantEnvelope,
  QuantOverviewData,
  RpsData,
  ScanData,
} from "../types/v2";
import SentimentGauge from "../components/quant/SentimentGauge.vue";
import BreadthTrendChart from "../components/quant/BreadthTrendChart.vue";
import MarginTrendChart from "../components/quant/MarginTrendChart.vue";
import ScanTable from "../components/quant/ScanTable.vue";
import DragonTigerTable from "../components/quant/DragonTigerTable.vue";
import LatestSessionPanel from "../components/quant/LatestSessionPanel.vue";
import {
  formatAmountYi,
  formatPct,
  getQuantReasonLabel,
  getScanTypeLabel,
  NO_DATA,
  SCAN_TYPE_LABELS,
} from "../domain/labels";

const overview = ref<QuantEnvelope<QuantOverviewData> | null>(null);
const breadth = ref<QuantEnvelope<BreadthPoint[]> | null>(null);
const margin = ref<QuantEnvelope<MarginPoint[]> | null>(null);
const dragon = ref<QuantEnvelope<DragonTigerItem[]> | null>(null);
const pools = ref<QuantEnvelope<LimitPoolsData> | null>(null);
const latest = ref<QuantEnvelope<LatestSessionData> | null>(null);
const scan = ref<QuantEnvelope<ScanData> | null>(null);
const rps = ref<QuantEnvelope<RpsData> | null>(null);
const activeTab = ref("ma_bull");
const loading = ref(false);
const error = ref<string | null>(null);

async function loadScan() {
  if (activeTab.value === "rps60") {
    scan.value = null;
    rps.value = await fetchQuantRps(60, 90, 50);
  } else {
    rps.value = null;
    scan.value = await fetchQuantScan(activeTab.value, 50);
  }
}

async function loadAll() {
  loading.value = true;
  try {
    const [ov, br, mg, dg, lp, ls] = await Promise.all([
      fetchQuantOverview(),
      fetchQuantBreadth(60),
      fetchQuantMargin(60),
      fetchQuantDragonTiger(30),
      fetchQuantLimitPools(10),
      fetchQuantLatestSession(),
    ]);
    overview.value = ov;
    breadth.value = br;
    margin.value = mg;
    dragon.value = dg;
    pools.value = lp;
    latest.value = ls;
    await loadScan();
    error.value = null;
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

onMounted(loadAll);
usePolling(loadAll, 5 * 60 * 1000, 5 * 60 * 1000);

const sentiment = computed(() => overview.value?.data?.sentiment ?? null);
const dataCutoff = computed(() => overview.value?.data_cutoff ?? null);
const totalAmountLabel = computed(() => formatAmountYi(sentiment.value?.total_amount ?? null));
const advanceRatioLabel = computed(() => {
  const ratio = sentiment.value?.advance_ratio;
  return typeof ratio === "number" ? formatPct(ratio * 100, 1) : NO_DATA;
});

function switchTab(tab: string) {
  activeTab.value = tab;
  loadScan();
}
</script>

<template>
  <div class="quant-lab">
    <div class="page-head">
      <p class="page-sub head-sub">
        直连本地量化数据库与当日行情源 · 形态扫描与情绪统计均为辅助口径，不构成投资建议
      </p>
      <div class="head-chips">
        <span v-if="dataCutoff" class="badge action">扫描数据截止 {{ dataCutoff }}</span>
        <span v-if="latest?.available" class="badge">最新落库快照 {{ latest?.data_cutoff }}</span>
        <button class="mini" @click="loadAll">{{ loading ? "刷新中…" : "刷新" }}</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>

    <!-- 情绪总览 -->
    <section v-if="overview?.available && overview.data" class="panel">
      <h3>
        历史情绪观察
        <span class="h3-extra">{{ overview.data.sentiment.trade_date }} · 全市场 {{ overview.data.universe_size }} 只 · 非正式口径</span>
      </h3>
      <div class="sentiment-grid">
        <SentimentGauge
          :temperature="overview.data.temperature"
          caption="辅助情绪温度 = 上涨占比 × 涨跌停强度"
        />
        <div class="stat-grid sentiment-cards">
          <div class="stat-card">
            <span class="stat-label">上涨 / 下跌 / 平盘</span>
            <span class="stat-value">
              <span class="up">{{ sentiment?.advancers }}</span>
              <span class="dim"> / </span>
              <span class="down">{{ sentiment?.decliners }}</span>
              <span class="dim"> / {{ sentiment?.unchanged }}</span>
            </span>
            <span class="stat-foot">上涨占比 {{ advanceRatioLabel }}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">涨停 / 跌停</span>
            <span class="stat-value">
              <span class="up">{{ sentiment?.limit_ups }}</span>
              <span class="dim"> / </span>
              <span class="down">{{ sentiment?.limit_downs }}</span>
            </span>
            <span class="stat-foot">炸板 {{ sentiment?.broken_boards }} 次</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">市场成交额</span>
            <span class="stat-value hero">{{ totalAmountLabel }}</span>
            <span class="stat-foot">
              中位涨跌幅
              {{ sentiment?.median_return === null ? NO_DATA : formatPct(sentiment?.median_return ?? null, 2) }}
            </span>
          </div>
          <div class="stat-card">
            <span class="stat-label">市场收益 / 宽度强度</span>
            <span class="stat-value">
              {{ sentiment?.market_return === null ? NO_DATA : formatPct(sentiment?.market_return ?? null, 2) }}
            </span>
            <span class="stat-foot">宽度强度 {{ sentiment?.breadth_strength?.toFixed(2) ?? NO_DATA }}</span>
          </div>
        </div>
      </div>
      <!-- 连板天梯 -->
      <div v-if="overview.data.consecutive_boards.length" class="boards-strip">
        <span class="strip-label">连板天梯</span>
        <span
          v-for="board in overview.data.consecutive_boards"
          :key="board.symbol"
          class="board-chip"
          :title="`${board.symbol} ${board.name} · 收盘 ${board.close ?? '—'}`"
        >
          <RouterLink class="board-link" :to="`/stocks/${board.symbol}`">{{ board.name || board.symbol }}</RouterLink>
          <b>{{ board.streak }}板</b>
        </span>
      </div>
    </section>
    <section v-else-if="overview && !overview.available" class="panel">
      <p class="dim">{{ getQuantReasonLabel(overview.reason_code) }}</p>
    </section>

    <!-- 当日快照 -->
    <section v-if="latest?.available && latest.data" class="panel">
      <h3>
        最新全市场快照
        <span class="h3-extra">{{ latest.data.trade_date }} · Tushare 补充</span>
      </h3>
      <LatestSessionPanel :session="latest.data" />
    </section>

    <!-- 宽度趋势 -->
    <section class="panel">
      <h3>
        市场宽度趋势（60交易日）
        <span class="h3-extra">上涨占比 + 涨跌停家数</span>
      </h3>
      <BreadthTrendChart v-if="breadth?.available && breadth.data.length" :points="breadth.data" />
      <p v-else class="dim">{{ breadth ? getQuantReasonLabel(breadth.reason_code) : "加载中…" }}</p>
    </section>

    <div class="grid-2">
      <!-- 两融 -->
      <section class="panel">
        <h3>两融余额趋势（60交易日）</h3>
        <MarginTrendChart v-if="margin?.available && margin.data.length" :points="margin.data" />
        <p v-else class="dim">{{ margin ? getQuantReasonLabel(margin.reason_code) : "加载中…" }}</p>
      </section>

      <!-- 涨跌停池 -->
      <section class="panel">
        <h3>
          涨跌停池
          <span class="h3-extra">{{ pools?.data_cutoff ?? "" }}</span>
        </h3>
        <div class="pool-cols">
          <div>
            <h4 class="pool-title up">涨停池（前10）</h4>
            <table v-if="pools?.available" class="data mini-pool">
              <tbody>
                <tr v-for="row in pools.data.limit_up_pool.slice(0, 10)" :key="row.symbol">
                  <td>
                    <RouterLink class="sym-link" :to="`/stocks/${row.symbol}`">{{ row.symbol }}</RouterLink>
                    <span class="sym-name">{{ row.name || "" }}</span>
                  </td>
                  <td class="num up">{{ row.return_pct === null || row.return_pct === undefined ? NO_DATA : formatPct(row.return_pct) }}</td>
                  <td class="num dim">{{ row.turnover_rate === null || row.turnover_rate === undefined ? NO_DATA : formatPct(row.turnover_rate, 1) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div>
            <h4 class="pool-title down">跌停池（前10）</h4>
            <table v-if="pools?.available" class="data mini-pool">
              <tbody>
                <tr v-for="row in pools.data.limit_down_pool.slice(0, 10)" :key="row.symbol">
                  <td>
                    <RouterLink class="sym-link" :to="`/stocks/${row.symbol}`">{{ row.symbol }}</RouterLink>
                    <span class="sym-name">{{ row.name || "" }}</span>
                  </td>
                  <td class="num down">{{ row.return_pct === null || row.return_pct === undefined ? NO_DATA : formatPct(row.return_pct) }}</td>
                  <td class="num dim">{{ row.turnover_rate === null || row.turnover_rate === undefined ? NO_DATA : formatPct(row.turnover_rate, 1) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>

    <!-- 龙虎榜（通栏） -->
    <section class="panel">
      <h3>
        龙虎榜
        <span class="h3-extra">{{ dragon?.data_cutoff ?? "" }} · 按净买额绝对值排序</span>
      </h3>
      <div class="table-scroll dragon-scroll">
        <DragonTigerTable v-if="dragon?.available" :items="dragon.data" />
        <p v-else class="dim pad">{{ dragon ? getQuantReasonLabel(dragon.reason_code) : "加载中…" }}</p>
      </div>
    </section>

    <!-- 扫描器 -->
    <section class="panel">
      <h3>
        全市场形态扫描
        <span class="h3-extra">
          {{ scan?.data_cutoff ?? rps?.data_cutoff ?? "" }} · 基于复权价 · 辅助口径
        </span>
      </h3>
      <div class="seg scan-seg">
        <button
          v-for="(label, key) in SCAN_TYPE_LABELS"
          :key="key"
          :class="{ active: activeTab === key }"
          @click="switchTab(key)"
        >
          {{ label }}
        </button>
        <button :class="{ active: activeTab === 'rps60' }" @click="switchTab('rps60')">
          RPS60强度 ≥ 90
        </button>
      </div>

      <div class="table-scroll scan-scroll">
        <template v-if="activeTab === 'rps60'">
          <table v-if="rps?.available" class="data">
            <thead>
              <tr>
                <th>#</th>
                <th>代码 / 名称</th>
                <th class="num">RPS60</th>
                <th class="num">60日涨幅</th>
                <th class="num">现价</th>
                <th class="num">涨跌幅</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in rps.data.items" :key="item.symbol">
                <td>
                  <span :class="['rank-no', { top1: index === 0, top2: index === 1, top3: index === 2 }]">{{ index + 1 }}</span>
                </td>
                <td>
                  <RouterLink class="sym-link" :to="`/stocks/${item.symbol}`">{{ item.symbol }}</RouterLink>
                  <span class="sym-name">{{ item.name || "" }}</span>
                </td>
                <td class="num rps-num">{{ item.rps?.toFixed(1) ?? NO_DATA }}</td>
                <td class="num up">{{ item.ret60_pct === null ? NO_DATA : formatPct(item.ret60_pct, 1) }}</td>
                <td class="num">{{ item.close ?? NO_DATA }}</td>
                <td class="num up">{{ item.return_pct === null ? NO_DATA : formatPct(item.return_pct) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="dim pad">{{ rps ? getQuantReasonLabel(rps.reason_code) : "加载中…" }}</p>
        </template>
        <template v-else>
          <ScanTable
            v-if="scan?.available"
            :rows="scan.data.items"
            :show-streak="false"
            :show-rps="false"
          />
          <p v-else class="dim pad">{{ scan ? getQuantReasonLabel(scan.reason_code) : "加载中…" }}</p>
        </template>
      </div>
      <p v-if="scan?.available" class="scan-total">
        命中 <b>{{ scan.data.total }}</b> 只，显示前 {{ scan.data.items.length }} 只（按涨跌幅排序）
      </p>
    </section>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}
.head-chips {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.sentiment-grid {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 18px;
  align-items: center;
}
@media (max-width: 900px) {
  .sentiment-grid { grid-template-columns: 1fr; }
}
.dim { color: var(--text-dim); }
.pad { padding: 18px; }
.boards-strip {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.strip-label {
  color: var(--text-dim);
  font-size: 12px;
  margin-right: 4px;
}
.board-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(255, 84, 100, 0.08);
  border: 1px solid rgba(255, 84, 100, 0.35);
  border-radius: 16px;
  padding: 3px 10px;
  font-size: 12px;
}
.board-chip b {
  color: var(--up);
  font-size: 11.5px;
}
.board-link {
  color: var(--text);
  text-decoration: none;
}
.board-link:hover { color: var(--accent); }
.head-sub {
  margin: 0;
}
.pool-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.pool-title {
  margin: 0 0 6px;
  font-size: 12.5px;
  font-weight: 600;
}
.mini-pool td {
  padding: 5px 6px;
  font-size: 12.5px;
}
.scan-seg { margin-bottom: 12px; }
.scan-scroll { max-height: 520px; }
.rps-num { color: var(--accent-2); font-weight: 700; }
.scan-total {
  color: var(--text-dim);
  font-size: 12px;
  margin: 10px 0 0;
}
.scan-total b { color: var(--accent); }
.dragon-scroll { max-height: 320px; }
</style>
