<script setup lang="ts">
import { computed } from "vue";
import type { CrossSectionItem } from "../../types/v2";
import ScoreCell from "./ScoreCell.vue";
import BucketCell from "./BucketCell.vue";
import {
  formatPct,
  NO_DATA,
  RANK_TOOLTIP,
} from "../../domain/labels";

const props = defineProps<{ items: CrossSectionItem[]; classificationSource: string }>();

type Row = CrossSectionItem & { route: string };

const rows = computed<Row[]>(() =>
  props.items.map((item) => ({ ...item, route: `/stocks/${item.symbol}` }))
);
</script>

<template>
  <div class="table-scroll" tabindex="0" aria-label="机会排名表，可横向滚动">
  <table class="opportunity-table">
    <thead>
      <tr>
        <th>股票</th>
        <th>行业</th>
        <th title="原始综合分线性换算为0—100">综合评分</th>
        <th :title="RANK_TOOLTIP">技术</th>
        <th :title="RANK_TOOLTIP">基本面</th>
        <th :title="RANK_TOOLTIP">情绪</th>
        <th :title="RANK_TOOLTIP">两融</th>
        <th>扫描层级</th>
        <th>现价</th>
        <th>涨跌幅</th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="!rows.length">
        <td colspan="10" class="dim">暂无数据</td>
      </tr>
      <tr v-for="row in rows" :key="row.symbol">
        <td>
          <RouterLink :to="row.route"><strong>{{ row.name || NO_DATA }}</strong><small>{{ row.symbol }}</small></RouterLink>
        </td>
        <td>{{ row.industry || NO_DATA }}</td>
        <td><ScoreCell :value="row.composite_score" /></td>
        <td><ScoreCell :value="row.tech_rank" rank /></td>
        <td><ScoreCell :value="row.fund_rank" rank /></td>
        <td><ScoreCell :value="row.sent_rank" rank /></td>
        <td><ScoreCell :value="row.margin_rank" rank /></td>
        <td>
          <BucketCell :bucket="row.scanner_bucket" :classification-source="classificationSource" />
        </td>
        <td>
          <template v-if="row.quote && row.quote.price !== null">{{ row.quote.price }}</template>
          <span v-else class="dim">{{ NO_DATA }}</span>
        </td>
        <td>
          <template v-if="row.quote && row.quote.change_pct !== null">
            <span :class="row.quote.change_pct > 0 ? 'up' : row.quote.change_pct < 0 ? 'down' : ''">
              {{ formatPct(row.quote.change_pct) }}
            </span>
          </template>
          <span v-else class="dim">{{ NO_DATA }}</span>
        </td>
      </tr>
    </tbody>
  </table>
  </div>
</template>

<style scoped>
.opportunity-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.opportunity-table th,
.opportunity-table td {
  border-bottom: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
}
.opportunity-table th {
  color: var(--text-dim);
  font-weight: 500;
}
.up { color: var(--up); }
.down { color: var(--down); }
.dim { color: var(--text-dim); }
.opportunity-table td a strong,.opportunity-table td a small{display:block}.opportunity-table td a small{margin-top:3px;color:var(--text-dim);font-size:11px}
</style>
