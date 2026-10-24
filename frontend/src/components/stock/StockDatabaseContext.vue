<script setup lang="ts">
import { computed } from "vue";
import type { Block, StockDatabaseContext } from "../../types/v2";
import {
  formatAmount,
  formatDate,
  formatDateTime,
  formatPct,
  formatScore,
  formatConfidence,
  getFinancialReportPeriod,
  getDataTypeLabel,
  getVerificationStatusLabel,
  NO_DATA,
} from "../../domain/labels";

const props = defineProps<{ context: Block<StockDatabaseContext> }>();
const data = computed(() => props.context.data);
const factorNames: Record<string, string> = { TECHNICAL: "技术", FUNDAMENTAL: "基本面", SENTIMENT: "情绪", POLICY_NEWS: "政策与事件", CAPITAL_FLOW: "资金流" };
const boardNames: Record<string, string> = { SH_MAIN: "沪市主板", SZ_MAIN: "深市主板", STAR: "科创板", CHINEXT: "创业板", BSE: "北交所" };
const listingStatusNames: Record<string, string> = { ACTIVE: "正常上市", LISTED: "正常上市", L: "正常上市", PAUSED: "暂停上市", P: "暂停上市", DELISTED: "已退市", D: "已退市", UNKNOWN: "状态未知" };
const metricNames: Record<string, string> = {
  basic_eps: "基本每股收益", diluted_eps: "稀释每股收益", total_revenue: "营业总收入",
  revenue: "营业收入", operate_profit: "营业利润", total_profit: "利润总额",
  n_income: "净利润", n_income_attr_p: "归母净利润", net_profit: "净利润",
  total_assets: "资产总额", total_liab: "负债总额", n_cashflow_act: "经营现金流净额",
  free_cashflow: "自由现金流", roe: "净资产收益率", gross_margin: "毛利率",
  revenue_yoy: "营收同比", netprofit_yoy: "净利润同比", debt_to_assets: "资产负债率",
};
const metricPriority = Object.keys(metricNames);
function scalarMetrics(value: unknown): Array<[string, string]> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const record = value as Record<string, unknown>;
  return metricPriority.filter((key) => record[key] !== null && record[key] !== undefined)
    .slice(0, 8).map((key) => {
      const raw = record[key];
      const ratio = ["roe", "gross_margin", "revenue_yoy", "netprofit_yoy", "debt_to_assets"].includes(key);
      const formatted = typeof raw === "number"
        ? ratio ? formatPct(Math.abs(raw) <= 1 ? raw * 100 : raw) : Math.abs(raw) >= 10000 ? formatAmount(raw) : String(raw)
        : String(raw);
      return [metricNames[key] ?? key, formatted];
    });
}
</script>

<template>
  <template v-if="context.available && data">
    <section class="panel profile-grid">
      <div><span>证券名称</span><strong>{{ data.identity.name || NO_DATA }}</strong></div>
      <div><span>所属行业</span><strong>{{ String(data.industry?.name ?? NO_DATA) }}</strong><small>{{ String(data.industry?.classification_system ?? '') }}</small></div>
      <div><span>上市板块</span><strong>{{ boardNames[data.identity.board ?? ''] ?? data.identity.board ?? NO_DATA }}</strong></div>
      <div><span>上市日期</span><strong>{{ formatDate(data.identity.list_date) }}</strong></div>
      <div><span>状态</span><strong>{{ data.identity.is_suspended ? '停牌' : data.identity.is_st ? 'ST' : listingStatusNames[String(data.identity.listing_status ?? 'UNKNOWN').toUpperCase()] ?? data.identity.listing_status ?? '状态未知' }}</strong><small v-if="data.identity.delist_date">摘牌 {{ formatDate(data.identity.delist_date) }}</small></div>
      <div><span>名称数据</span><strong>{{ getVerificationStatusLabel(data.identity.verification_status) }}</strong><small>{{ data.identity.source ?? '' }}</small></div>
    </section>

    <div class="grid-2">
      <section class="panel">
        <h3>最新行情快照</h3>
        <div v-if="data.quote" class="quote-grid">
          <div><span>现价</span><strong>{{ data.quote.price ?? NO_DATA }}</strong></div>
          <div><span>涨跌幅</span><strong>{{ data.quote.change_pct == null ? NO_DATA : formatPct(Number(data.quote.change_pct)) }}</strong></div>
          <div><span>开 / 高 / 低</span><strong>{{ data.quote.open ?? NO_DATA }} / {{ data.quote.high ?? NO_DATA }} / {{ data.quote.low ?? NO_DATA }}</strong></div>
          <div><span>换手率</span><strong>{{ data.quote.turnover_rate == null ? NO_DATA : formatPct(Number(data.quote.turnover_rate)) }}</strong></div>
          <div><span>成交额</span><strong>{{ data.quote.amount == null ? NO_DATA : formatAmount(Number(data.quote.amount)) }}</strong></div>
          <div><span>截至</span><strong>{{ formatDateTime(String(data.quote.snapshot_time ?? '')) }}</strong></div>
        </div><p v-else class="dim">暂无该股票行情快照。</p>
      </section>
      <section class="panel">
        <h3>策略因子明细</h3>
        <div v-if="data.factors.length" class="factor-list">
          <div v-for="factor in data.factors" :key="String(factor.type)">
            <span>{{ factorNames[String(factor.type)] ?? factor.type }}</span><strong>{{ formatScore(factor.score) }}</strong>
            <small>置信度 {{ formatConfidence(factor.confidence) }} · {{ factor.formal_status === 'shadow' ? '影子研究' : '正式结果' }} · 截至 {{ formatDateTime(String(factor.data_cutoff ?? '')) }}</small>
          </div>
        </div><p v-else class="dim">暂无已持久化的策略因子明细；四维横截面仍可单独查看。</p>
      </section>
    </div>

    <section class="panel">
      <h3>财务与基本面记录</h3>
      <div v-if="data.financials.length" class="financial-grid">
        <article v-for="record in data.financials" :key="`${record.data_type}-${record.data_cutoff}`">
          <header><strong>{{ getDataTypeLabel(record.data_type) }}</strong><span>{{ getVerificationStatusLabel(record.verification_status) }} · {{ record.source }}</span></header>
          <dl><template v-for="metric in scalarMetrics(record.metrics)" :key="metric[0]"><dt>{{ metric[0] }}</dt><dd>{{ metric[1] }}</dd></template></dl>
          <small>报告期 {{ getFinancialReportPeriod(record.metrics) }} · 来源事件日 {{ formatDate(String(record.event_time ?? '')) }} · 数据截至 {{ formatDateTime(String(record.data_cutoff ?? '')) }}</small>
        </article>
      </div><p v-else class="dim">数据库暂无该股票的规范化财务记录。</p>
    </section>
  </template>
  <section v-else class="panel"><h3>数据库个股档案</h3><p class="dim">当前接口未部署或该股票未进入版本化股票池。</p></section>
</template>

<style scoped>
.profile-grid{display:grid;grid-template-columns:repeat(6,minmax(110px,1fr));gap:14px}.profile-grid span,.quote-grid span,.factor-list span{display:block;color:var(--text-dim);font-size:11px}.profile-grid strong,.quote-grid strong{display:block;margin-top:6px}.profile-grid small,.factor-list small{display:block;margin-top:4px;color:var(--text-dim);font-size:11px}.quote-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.factor-list{display:grid;gap:10px}.factor-list>div{padding:9px 11px;background:var(--bg-hover);border-radius:7px}.factor-list strong{float:right}.financial-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.financial-grid article{padding:12px;background:var(--bg-hover);border-radius:8px}.financial-grid header{display:flex;justify-content:space-between;gap:12px}.financial-grid header span,.financial-grid small{color:var(--text-dim);font-size:11px}.financial-grid dl{display:grid;grid-template-columns:1fr auto;gap:4px 12px}.financial-grid dt{color:var(--text-dim);overflow:hidden;text-overflow:ellipsis}.financial-grid dd{margin:0;font-variant-numeric:tabular-nums}.dim{color:var(--text-dim)}@media(max-width:1100px){.profile-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.profile-grid,.financial-grid{grid-template-columns:1fr 1fr}}
</style>
