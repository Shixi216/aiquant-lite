<script setup lang="ts">
import type { LimitPoolRow, RpsItem, ScanItem } from "../../types/v2";
import { formatPct, NO_DATA } from "../../domain/labels";

type Row = Partial<ScanItem & RpsItem & LimitPoolRow> & {
  symbol: string;
  name?: string | null;
  streak?: number;
  rps?: number | null;
  ret60_pct?: number | null;
};

const props = defineProps<{
  rows: Row[];
  showRps?: boolean;
  showStreak?: boolean;
}>();

function pctClass(v: number | null | undefined): string {
  if (typeof v !== "number") return "";
  return v > 0 ? "up" : v < 0 ? "down" : "";
}
</script>

<template>
  <table class="data">
    <thead>
      <tr>
        <th>#</th>
        <th>代码 / 名称</th>
        <th v-if="showStreak">连板</th>
        <th v-if="showRps" class="num">RPS</th>
        <th v-if="showRps" class="num">60日涨幅</th>
        <th class="num">现价</th>
        <th class="num">涨跌幅</th>
        <th class="num">量比</th>
        <th class="num">换手率</th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="!rows.length">
        <td colspan="9" class="empty-inline">暂无符合条件标的</td>
      </tr>
      <tr v-for="(row, index) in rows" :key="row.symbol">
        <td>
          <span :class="['rank-no', { top1: index === 0, top2: index === 1, top3: index === 2 }]">
            {{ index + 1 }}
          </span>
        </td>
        <td>
          <RouterLink class="sym-link" :to="`/stocks/${row.symbol}`">{{ row.symbol }}</RouterLink>
          <span class="sym-name">{{ row.name || "" }}</span>
        </td>
        <td v-if="showStreak">
          <span class="streak-badge">{{ row.streak }}板</span>
        </td>
        <td v-if="showRps" class="num rps-cell">{{ row.rps ?? NO_DATA }}</td>
        <td v-if="showRps" class="num" :class="pctClass(row.ret60_pct)">
          {{ row.ret60_pct === null || row.ret60_pct === undefined ? NO_DATA : formatPct(row.ret60_pct, 1) }}
        </td>
        <td class="num">{{ row.close ?? NO_DATA }}</td>
        <td class="num" :class="pctClass(row.return_pct)">
          {{ row.return_pct === null || row.return_pct === undefined ? NO_DATA : formatPct(row.return_pct) }}
        </td>
        <td class="num">{{ row.volume_ratio?.toFixed(2) ?? NO_DATA }}</td>
        <td class="num">
          {{ row.turnover_rate === null || row.turnover_rate === undefined ? NO_DATA : formatPct(row.turnover_rate, 2) }}
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.empty-inline {
  text-align: center;
  color: var(--text-dim);
  padding: 24px;
}
.streak-badge {
  color: var(--up);
  background: rgba(255, 84, 100, 0.12);
  border: 1px solid rgba(255, 84, 100, 0.4);
  border-radius: 8px;
  padding: 1px 8px;
  font-size: 11.5px;
  font-weight: 700;
}
.rps-cell {
  color: var(--accent-2);
  font-weight: 700;
}
</style>
