<script setup lang="ts">
import { computed } from "vue";
import type { Block, MarketContextData } from "../../types/v2";
import { formatAmount, formatDateTime, formatPct, formatScore, formatConfidence } from "../../domain/labels";
import { displayableResearchFactor } from "../../domain/dashboardDisplay";

const props = defineProps<{ context: Block<MarketContextData> }>();
const regime: Record<string, string> = { RISK_ON: "偏强", BALANCED: "均衡", RISK_OFF: "偏弱" };
const sentiment = computed(() => displayableResearchFactor(props.context.data?.macro.sentiment));
const capitalFlow = computed(() => displayableResearchFactor(props.context.data?.macro.capital_flow));
const contextDescription = computed(() => {
  const extras = [sentiment.value ? "情绪" : "", capitalFlow.value ? "资金流" : ""].filter(Boolean);
  return extras.length
    ? `基于全市场涨跌分布、成交数据与高置信度${extras.join("、")}研究`
    : "基于全市场涨跌分布与成交数据";
});
const showSectorScores = computed(() =>
  Boolean(props.context.data?.sectors.some((sector) => Number.isFinite(sector.average_composite_score))),
);
</script>

<template>
  <section class="panel">
    <div class="section-head"><div><h3>宏观与市场环境</h3><p>{{ contextDescription }}</p></div><span v-if="context.available" class="badge">截至 {{ formatDateTime(context.data?.data_cutoff) }}</span></div>
    <div v-if="context.available && context.data" class="macro-grid">
      <div v-if="regime[context.data.macro.market_regime]" class="metric"><span>市场状态</span><strong>{{ regime[context.data.macro.market_regime] }}</strong></div>
      <div v-if="context.data.macro.advance_ratio != null" class="metric"><span>上涨占比</span><strong>{{ formatPct(context.data.macro.advance_ratio * 100) }}</strong></div>
      <div v-if="context.data.macro.median_change_pct != null" class="metric"><span>中位涨跌</span><strong>{{ formatPct(context.data.macro.median_change_pct) }}</strong></div>
      <div v-if="context.data.macro.total_amount != null" class="metric"><span>全市场成交额</span><strong>{{ formatAmount(context.data.macro.total_amount) }}</strong></div>
      <div v-if="sentiment" class="metric"><span>市场情绪</span><strong>{{ formatScore(sentiment.score) }}</strong><small>影子研究 · 截至 {{ formatDateTime(sentiment.data_cutoff) }} · 置信度 {{ formatConfidence(sentiment.confidence) }}</small></div>
      <div v-if="capitalFlow" class="metric"><span>市场资金流</span><strong>{{ formatScore(capitalFlow.score) }}</strong><small>影子研究 · 截至 {{ formatDateTime(capitalFlow.data_cutoff) }} · 置信度 {{ formatConfidence(capitalFlow.confidence) }}</small></div>
    </div>
    <p v-else class="dim">该数据库暂没有可用的市场环境聚合。</p>
  </section>
  <section class="panel">
    <div class="section-head"><div><h3>全部板块现状</h3><p>按数据库行业映射聚合当期全市场快照，按板块中位涨跌排序</p></div></div>
    <div v-if="context.available && context.data?.sectors.length" class="table-scroll" tabindex="0">
      <table class="data">
        <thead><tr><th>板块</th><th>成分数</th><th>上涨/下跌</th><th>上涨占比</th><th>中位涨跌</th><th v-if="showSectorScores">平均四维分</th><th>成交额</th></tr></thead>
        <tbody><tr v-for="sector in context.data.sectors" :key="sector.sector">
          <td><strong>{{ sector.sector }}</strong><small>{{ sector.classification_system }}</small></td>
          <td>{{ sector.member_count }}</td><td>{{ sector.advances }} / {{ sector.declines }}</td>
          <td>{{ formatPct(sector.advance_ratio * 100) }}</td>
          <td :class="sector.median_change_pct && sector.median_change_pct > 0 ? 'up' : sector.median_change_pct && sector.median_change_pct < 0 ? 'down' : ''">{{ formatPct(sector.median_change_pct) }}</td>
          <td v-if="showSectorScores">{{ formatScore(sector.average_composite_score) }}</td><td>{{ formatAmount(sector.total_amount) }}</td>
        </tr></tbody>
      </table>
    </div>
    <p v-else class="dim">行业映射或板块行情暂无覆盖。</p>
  </section>
</template>

<style scoped>
.section-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.section-head h3{margin-bottom:4px}.section-head p,.dim{margin:0;color:var(--text-dim);font-size:12px}.macro-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.metric{padding:13px;background:var(--bg-hover);border-radius:8px}.metric span,.metric small,td small{display:block;color:var(--text-dim);font-size:11px}.metric strong{display:block;margin-top:8px;font-size:17px}td small{margin-top:3px}@media(max-width:640px){.macro-grid{grid-template-columns:repeat(2,1fr)}}
</style>
