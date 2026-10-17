<script setup lang="ts">
import type { DragonTigerItem } from "../../types/v2";
import { formatAmountYi, NO_DATA } from "../../domain/labels";

const props = defineProps<{ items: DragonTigerItem[] }>();

function pctClass(v: number | null | undefined): string {
  if (typeof v !== "number") return "";
  return v > 0 ? "up" : v < 0 ? "down" : "";
}
</script>

<template>
  <table class="data">
    <thead>
      <tr>
        <th>代码 / 名称</th>
        <th>上榜原因</th>
        <th class="num">龙虎净买</th>
        <th class="num">机构净买</th>
        <th class="num">买入</th>
        <th class="num">卖出</th>
        <th class="num">席位</th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="!items.length">
        <td colspan="7" class="empty-inline">当日无龙虎榜数据</td>
      </tr>
      <tr v-for="item in items" :key="item.symbol">
        <td>
          <RouterLink class="sym-link" :to="`/stocks/${item.symbol}`">{{ item.symbol }}</RouterLink>
          <span class="sym-name">{{ item.name || "" }}</span>
        </td>
        <td class="reason-cell">
          <span v-for="reason in item.reasons" :key="reason" class="badge warn reason-tag">{{ reason }}</span>
        </td>
        <td class="num" :class="pctClass(item.net_amount)">
          {{ formatAmountYi(item.net_amount) }}
        </td>
        <td class="num" :class="pctClass(item.institution_net_amount)">
          {{ item.institution_net_amount === null ? NO_DATA : formatAmountYi(item.institution_net_amount) }}
        </td>
        <td class="num">{{ formatAmountYi(item.buy_amount) }}</td>
        <td class="num">{{ formatAmountYi(item.sell_amount) }}</td>
        <td class="num">{{ item.seat_count ?? NO_DATA }} / 机构 {{ item.institution_seat_count ?? NO_DATA }}</td>
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
.reason-cell {
  max-width: 320px;
  white-space: normal !important;
}
.reason-tag {
  margin-right: 5px;
  margin-bottom: 2px;
  display: inline-block;
}
</style>
