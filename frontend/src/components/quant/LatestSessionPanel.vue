<script setup lang="ts">
import { computed } from "vue";
import type { LatestSessionData, LatestSessionStock } from "../../types/v2";
import { formatAmountYi, formatPct, NO_DATA } from "../../domain/labels";

const props = defineProps<{ session: LatestSessionData }>();

const boards = computed(() => [
  { key: "gainers", label: "涨幅榜", rows: props.session.gainers },
  { key: "losers", label: "跌幅榜", rows: props.session.losers },
  { key: "top_amount", label: "成交额榜", rows: props.session.top_amount },
]);

function pctClass(v: number | null | undefined): string {
  if (typeof v !== "number") return "";
  return v > 0 ? "up" : v < 0 ? "down" : "";
}

function amountValue(stock: LatestSessionStock): string {
  if (stock.amount === null || stock.amount === undefined) return NO_DATA;
  return formatAmountYi(stock.amount);
}
</script>

<template>
  <div class="latest-panel">
    <div class="stat-grid summary">
      <div class="stat-card">
        <span class="stat-label">上涨 / 下跌 / 平盘</span>
        <span class="stat-value">
          <span class="up">{{ session.advancers }}</span>
          <span class="dim"> / </span>
          <span class="down">{{ session.decliners }}</span>
          <span class="dim"> / {{ session.flats }}</span>
        </span>
      </div>
      <div class="stat-card">
        <span class="stat-label">涨停(近似) / 跌停(近似)</span>
        <span class="stat-value">
          <span class="up">{{ session.limit_ups_approx }}</span>
          <span class="dim"> / </span>
          <span class="down">{{ session.limit_downs_approx }}</span>
        </span>
      </div>
      <div class="stat-card">
        <span class="stat-label">两市成交额</span>
        <span class="stat-value hero">{{ formatAmountYi(session.total_amount) }}</span>
      </div>
    </div>
    <p class="note">{{ session.note }}</p>
    <div class="board-cols">
      <div v-for="board in boards" :key="board.key" class="board-col">
        <h4>{{ board.label }}</h4>
        <table class="data mini-table">
          <tbody>
            <tr v-for="stock in board.rows.slice(0, 10)" :key="stock.symbol">
              <td>
                <RouterLink class="sym-link" :to="`/stocks/${stock.symbol}`">{{ stock.symbol }}</RouterLink>
                <span class="sym-name">{{ stock.name || "" }}</span>
              </td>
              <td class="num">{{ stock.close ?? NO_DATA }}</td>
              <td class="num" :class="pctClass(stock.return_pct)">
                {{ stock.return_pct === null || stock.return_pct === undefined ? NO_DATA : formatPct(stock.return_pct) }}
              </td>
              <td class="num dim">{{ amountValue(stock) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.summary {
  margin-bottom: 10px;
}
.note {
  color: var(--text-dim);
  font-size: 11.5px;
  margin: 0 0 12px;
}
.board-cols {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
@media (max-width: 1100px) {
  .board-cols { grid-template-columns: 1fr; }
}
.board-col h4 {
  margin: 0 0 6px;
  font-size: 12.5px;
  color: var(--text-dim);
  font-weight: 600;
}
.mini-table td {
  padding: 5px 6px;
  font-size: 12.5px;
}
.dim { color: var(--text-dim); }
</style>
