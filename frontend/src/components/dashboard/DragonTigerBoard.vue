<script setup lang="ts">
import type { Block, DragonTigerBoardData } from "../../types/v2";
import { formatAmount, formatDate, formatDateTime, NO_DATA } from "../../domain/labels";

defineProps<{ board: Block<DragonTigerBoardData> }>();
</script>

<template>
  <section class="panel">
    <div class="section-head">
      <div><h3>龙虎榜</h3><p>交易所盘后公开事件，不是盘中实时资金流</p></div>
      <div v-if="board.available && board.data" class="cutoff">
        交易日 {{ formatDate(board.data.trade_date) }} · 共 {{ board.data.total }} 只<small>约盘后发布 · 可用时间 {{ formatDateTime(board.data.available_time) }}</small>
      </div>
    </div>
    <div v-if="board.available && board.data?.items.length" class="table-scroll" tabindex="0" aria-label="龙虎榜，可横向滚动">
      <table class="data">
        <thead><tr><th>股票</th><th>上榜原因</th><th>买入额</th><th>卖出额</th><th>净额</th><th>机构席位净额</th><th>席位</th></tr></thead>
        <tbody><tr v-for="item in board.data.items" :key="item.symbol">
          <td><RouterLink :to="`/stocks/${item.symbol}`"><strong>{{ item.name || NO_DATA }}</strong><small>{{ item.symbol }}</small></RouterLink></td>
          <td class="reason" :title="item.reasons.join('；')">{{ item.reasons.join('；') || NO_DATA }}</td>
          <td>{{ formatAmount(item.buy_amount) }}</td><td>{{ formatAmount(item.sell_amount) }}</td>
          <td :class="item.net_amount && item.net_amount > 0 ? 'up' : item.net_amount && item.net_amount < 0 ? 'down' : ''">{{ formatAmount(item.net_amount) }}</td>
          <td :class="item.institution_net_amount && item.institution_net_amount > 0 ? 'up' : item.institution_net_amount && item.institution_net_amount < 0 ? 'down' : ''">{{ formatAmount(item.institution_net_amount) }}</td>
          <td>{{ item.institution_seat_count ?? 0 }} 机构 / {{ item.seat_count ?? NO_DATA }} 总计</td>
        </tr></tbody>
      </table>
    </div>
    <p v-else class="dim">数据库暂无可用龙虎榜事件。该数据仅在交易日收盘后由数据源发布。</p>
    <p v-if="board.available && board.data" class="source">来源：{{ board.data.source }} · 本页按净额绝对值排序</p>
  </section>
</template>

<style scoped>
.section-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.section-head h3{margin-bottom:4px}.section-head p,.dim,.source{margin:0;color:var(--text-dim);font-size:12px}.cutoff{text-align:right;color:var(--warn);font-size:12px}.cutoff small,td small{display:block;color:var(--text-dim);font-size:11px;margin-top:3px}.reason{max-width:320px;overflow:hidden;text-overflow:ellipsis}.source{margin-top:10px}a{color:var(--accent);text-decoration:none}@media(max-width:760px){.section-head{display:block}.cutoff{text-align:left;margin-top:8px}}
</style>
