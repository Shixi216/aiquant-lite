import * as echarts from "echarts/core";
import { CandlestickChart, LineChart, BarChart } from "echarts/charts";
import {
  DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent,
  MarkLineComponent, TitleComponent, TooltipComponent, VisualMapComponent,
} from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
echarts.use([CandlestickChart, LineChart, BarChart, DataZoomComponent, GridComponent,
  LegendComponent, MarkAreaComponent, MarkLineComponent, TitleComponent,
  TooltipComponent, VisualMapComponent, SVGRenderer]);
const UP_COLOR = "#f04848", DOWN_COLOR = "#2ebd85";
const bars = [
  {"trade_date":"2026-06-02","open":1245.0,"high":1262.0,"low":1238.0,"close":1258.0,"volume":23100,"ma5":1242.5,"ma20":1229.6},
  {"trade_date":"2026-06-03","open":1258.0,"high":1266.0,"low":1250.0,"close":1262.0,"volume":21500,"ma5":1246.8,"ma20":1231.1},
  {"trade_date":"2026-06-04","open":1262.0,"high":1278.0,"low":1259.0,"close":1275.0,"volume":26800,"ma5":1252.4,"ma20":1233.2},
];
const dates = bars.map(b => b.trade_date);
const candles = bars.map(b => [b.open, b.close, b.low, b.high]);
const option = {
  animation: false,
  legend: { data: ["MA5", "MA20"] },
  grid: [
    { left: 60, right: 20, top: 30, height: "58%" },
    { left: 60, right: 20, top: "74%", height: "16%" },
  ],
  xAxis: [
    { type: "category", data: dates, gridIndex: 0, boundaryGap: true },
    { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false } },
  ],
  yAxis: [
    { scale: true, gridIndex: 0 },
    { gridIndex: 1, axisLabel: { show: false } },
  ],
  dataZoom: [
    { type: "inside", xAxisIndex: [0, 1] },
    { type: "slider", xAxisIndex: [0, 1], top: "94%" },
  ],
  series: [
    {
      name: "日K", type: "candlestick", data: candles, xAxisIndex: 0, yAxisIndex: 0,
      itemStyle: { color: UP_COLOR, color0: DOWN_COLOR, borderColor: UP_COLOR, borderColor0: DOWN_COLOR },
      markArea: { silent: true, data: [[
        { yAxis: 1268, itemStyle: { color: "rgba(77,163,255,0.18)" }, label: { formatter: "允许建仓区", position: "insideTopRight" } },
        { yAxis: 1292 },
      ]] },
      markLine: { silent: true, symbol: "none", data: [{ yAxis: 1215, lineStyle: { color: "#f04848", type: "solid" }, label: { formatter: "止损 1215", position: "insideEndTop" } }] },
    },
    { name: "MA5", type: "line", data: bars.map(b => b.ma5), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false },
    { name: "MA20", type: "line", data: bars.map(b => b.ma20), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false },
    { name: "成交量", type: "bar", data: bars.map(b => ({ value: b.volume, itemStyle: { color: b.close >= b.open ? UP_COLOR : DOWN_COLOR } })), xAxisIndex: 1, yAxisIndex: 1 },
  ],
};
const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 1143, height: 460 });
chart.setOption(option);
const svg = chart.renderToSVGString();
console.log(JSON.stringify({
  ok: true,
  redFill: svg.includes("#f04848"),
  greenFill: svg.includes("#2ebd85"),
  maLegend: svg.includes("MA5"),
  markAreaText: svg.includes("允许建仓区"),
  markLineText: svg.includes("止损 1215"),
  pathCount: (svg.match(/<path/g) ?? []).length,
  svgLen: svg.length,
}, null, 2));
