<script setup lang="ts">
import { computed } from "vue";
import type { DecisionPacketData, ResearchReview } from "../../types/v2";
import {
  getDecisionLabel,
  formatDateTime,
  NO_DATA,
} from "../../domain/labels";

const props = defineProps<{ packet: DecisionPacketData | null; reason?: string | null; review?: ResearchReview | null }>();

const zoneText = computed(() => {
  const packet = props.packet;
  if (!packet) return NO_DATA;
  if (!packet.has_frozen_plan || !packet.frozen_entry_zone) return NO_DATA;
  return `${packet.frozen_entry_zone.low} ~ ${packet.frozen_entry_zone.high}`;
});
const preferredText = computed(() => {
  const zone = props.packet?.frozen_preferred_zone;
  if (!zone) return NO_DATA;
  return `${zone.low} ~ ${zone.high}`;
});
</script>

<template>
  <section class="panel decision-card">
    <template v-if="!packet && review">
      <div class="head"><span class="symbol">本轮研究结论</span><strong class="action">{{ review.action_zh }}</strong></div>
      <p class="frozen-note">{{ review.execution_status }} · 行情截至 {{ review.price_as_of }} · 研究生成 {{ formatDateTime(review.generated_at) }}</p>
      <p class="frozen-note">研究建议，尚未保存为冻结交易计划。最终操作由使用者自行执行。</p>
    </template>
    <div v-else-if="!packet" class="empty">
      暂无冻结交易计划
      <span v-if="reason" class="dim">（{{ reason }}）</span>
    </div>
    <template v-else>
      <div class="head">
        <span class="symbol">{{ packet.symbol }}</span>
        <span :class="['action', packet.action]">{{ getDecisionLabel(packet.action, packet.action_zh) }}</span>
        <span v-if="packet.zone_expired" class="expired">
          计划已过期（{{ formatDateTime(packet.zone_valid_until) }}）
        </span>
      </div>
      <dl class="grid">
        <div><dt>参考价</dt><dd>{{ packet.reference_price || NO_DATA }}</dd></div>
        <div><dt>允许建仓区</dt><dd>{{ zoneText }}</dd></div>
        <div><dt>优选建仓区</dt><dd>{{ preferredText }}</dd></div>
        <div><dt>止损位</dt><dd>{{ packet.frozen_stop_loss_price ?? NO_DATA }}</dd></div>
        <div><dt>置信度</dt><dd>{{ Number.isFinite(packet.confidence) ? (packet.confidence * 100).toFixed(0) + "%" : NO_DATA }}</dd></div>
        <div><dt>数据截至</dt><dd>{{ formatDateTime(packet.data_cutoff) }}</dd></div>
        <div><dt>决策产生</dt><dd>{{ formatDateTime(packet.zone_generated_at) }}</dd></div>
        <div><dt>有效期至</dt><dd>{{ formatDateTime(packet.zone_valid_until) }}</dd></div>
      </dl>
      <p class="frozen-note">区间为决策时冻结值，原样展示，不随实时价格重算。</p>
    </template>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.symbol {
  font-size: 18px;
  font-weight: 700;
}
.action {
  font-size: 16px;
  font-weight: 600;
  color: var(--accent);
}
.action.STRONG_BUY,
.action.BUY,
.action.ADD {
  color: var(--up);
}
.action.REDUCE,
.action.STOP_LOSS,
.action.EXIT,
.action.TAKE_PROFIT,
.action.AVOID {
  color: var(--down);
}
.expired {
  color: var(--warn);
  font-size: 12px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px 16px;
  margin: 12px 0 0;
}
.grid dt {
  font-size: 12px;
  color: var(--text-dim);
}
.grid dd {
  margin: 2px 0 0;
  font-weight: 600;
}
.frozen-note {
  color: var(--text-dim);
  font-size: 12px;
  margin: 10px 0 0;
}
.empty {
  color: var(--text-dim);
  padding: 12px 0;
}
.dim {
  color: var(--text-dim);
}
</style>
