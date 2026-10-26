<script setup lang="ts">
import { computed, ref, watchEffect } from "vue";
import type { Ref } from "vue";
import type { ChartBar, DecisionPacketData } from "../../types/v2";
import { type EChartsCoreOption } from "../../charts";
import { useECharts } from "../../composables/useECharts";
import { useChartTheme, withAlpha } from "../../domain/chartTheme";
import { DOWN_COLOR, UP_COLOR, formatAmount, formatVolume, formatPct, hasInconsistentTurnover } from "../../domain/labels";

const props = defineProps<{
  bars: ChartBar[];
  zones: DecisionPacketData | null;
}>();

const el = ref<HTMLElement | null>(null);
const { setOption } = useECharts(el as Ref<HTMLElement | null>);
const ct = useChartTheme();

/** 决策区间/止损止盈取色：computed 随主题联动（切换主题后重绘自动生效）。 */
const MARK_COLORS = computed(() => ({
  entry: withAlpha(ct.value.accent, 0.18),
  preferred: withAlpha(ct.value.warn, 0.22),
  stop: ct.value.up,
  takeProfit: ct.value.down,
}));

function zoneMarkLine(packet: DecisionPacketData) {
  const lines: Record<string, unknown>[] = [];
  if (packet.frozen_stop_loss_price !== null) {
    lines.push({
      yAxis: packet.frozen_stop_loss_price,
      lineStyle: { color: MARK_COLORS.value.stop, type: "solid" },
      label: { formatter: `止损 ${packet.frozen_stop_loss_price}`, position: "insideEndTop" },
    });
  }
  return lines;
}

function zoneMarkAreas(packet: DecisionPacketData) {
  const areas: Record<string, unknown>[][] = [];
  if (packet.frozen_entry_zone) {
    areas.push([
      {
        yAxis: packet.frozen_entry_zone.low,
        itemStyle: { color: MARK_COLORS.value.entry },
        label: { formatter: "允许建仓区", position: "insideTopRight" },
      },
      { yAxis: packet.frozen_entry_zone.high },
    ]);
  }
  if (packet.frozen_preferred_zone) {
    areas.push([
      {
        yAxis: packet.frozen_preferred_zone.low,
        itemStyle: { color: MARK_COLORS.value.preferred },
        label: { formatter: "优选建仓区", position: "insideTopRight" },
      },
      { yAxis: packet.frozen_preferred_zone.high },
    ]);
  }
  return areas;
}

const option = computed<EChartsCoreOption | null>(() => {
  const bars = props.bars;
  if (!bars.length) return null;
  const dates = bars.map((bar) => bar.trade_date);
  const candles = bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]);
  const volumes = bars.map((bar) => ({
    value: hasInconsistentTurnover(bar) ? null : bar.volume,
    itemStyle: {
      color: (bar.close ?? 0) >= (bar.open ?? 0) ? UP_COLOR : DOWN_COLOR,
    },
  }));
  const series: Record<string, unknown>[] = [
    {
      name: "日K",
      type: "candlestick",
      data: candles,
      xAxisIndex: 0,
      yAxisIndex: 0,
      itemStyle: { color: UP_COLOR, color0: DOWN_COLOR, borderColor: UP_COLOR, borderColor0: DOWN_COLOR },
      ...(props.zones
        ? {
            markArea: { silent: true, data: zoneMarkAreas(props.zones) },
            markLine: { silent: true, symbol: "none", data: zoneMarkLine(props.zones) },
          }
        : {}),
    },
    { name: "MA5", type: "line", data: bars.map((b) => b.ma5), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false },
    { name: "MA20", type: "line", data: bars.map((b) => b.ma20), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false },
    {
      name: "成交量",
      type: "bar",
      data: volumes,
      xAxisIndex: 1,
      yAxisIndex: 1,
    },
  ];
  const legendNames = ["MA5", "MA20"];
  if (bars.some((b) => b.ma10 !== null)) {
    series.push({
      name: "MA10",
      type: "line",
      data: bars.map((b) => b.ma10),
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
    });
    legendNames.push("MA10");
  }
  if (bars.some((b) => b.ma60 !== null)) {
    series.push({
      name: "MA60",
      type: "line",
      data: bars.map((b) => b.ma60),
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
    });
    legendNames.push("MA60");
  }
  return {
    animation: false,
    legend: { data: legendNames.sort((a, b) => Number(a.slice(2)) - Number(b.slice(2))), top: 0, textStyle: { color: ct.value.text }, inactiveColor: ct.value.faint },
    tooltip: {
      trigger: "axis",
      confine: true,
      axisPointer: { type: "cross" },
      formatter: (params: unknown) => {
        const list = params as { seriesName: string; value: unknown; axisValue: string }[];
        const point = list.find((item) => item.seriesName === "日K") ?? list[0];
        if (!point) return "";
        const bar = bars.find((b) => b.trade_date === point.axisValue);
        if (!bar) return "";
        const price = (value: number | null) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
        const change = bar?.change_pct;
        const rows = [
          `日期：${bar.trade_date}`,
          `开盘：${price(bar.open)}`,
          `最高：${price(bar.high)}`,
          `最低：${price(bar.low)}`,
          `收盘：${price(bar.close)}`,
          `涨跌：${formatPct(change)}`,
          `成交量：${formatVolume(hasInconsistentTurnover(bar) ? null : bar.volume)}`,
          `成交额：${formatAmount(hasInconsistentTurnover(bar) ? null : bar.amount)}`,
        ];
        return rows.join("<br/>");
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 60, right: 20, top: 30, height: "58%" },
      { left: 60, right: 20, top: "74%", height: "16%" },
    ],
    xAxis: [
      { type: "category", data: dates, gridIndex: 0, boundaryGap: true, axisLabel: { color: ct.value.dim }, axisLine: { lineStyle: { color: ct.value.axisLine } } },
      { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: ct.value.grid } } },
      { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], top: "94%" },
    ],
    series,
  } as EChartsCoreOption;
});

watchEffect(() => { if (option.value) setOption(option.value, true); });
</script>

<template>
  <div class="kline-wrap">
    <div v-if="bars.length" ref="el" class="kline" role="img" :aria-label="`日K与成交量图，共${bars.length}个交易日`"></div>
    <div v-else class="kline-empty">暂无K线数据</div>
  </div>
</template>

<style scoped>
.kline {
  height: 460px;
}
.kline-empty {
  height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-dim);
  border: 1px dashed var(--border);
  border-radius: 6px;
}
</style>
