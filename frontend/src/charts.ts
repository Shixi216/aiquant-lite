import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, HeatmapChart, LineChart, PieChart, SankeyChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  CandlestickChart,
  HeatmapChart,
  SankeyChart,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  DataZoomComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

/* 主题基座：坐标轴/图例/提示框默认取色由主题提供；
 * 组件 option 里的显式颜色会覆盖这里，建议组件内改用 domain/chartTheme.ts。 */
const AXIS_LIGHT = { color: "#5f7469" };
const AXIS_DARK = { color: "#8595aa" };

function baseTheme(mode: "light" | "dark") {
  const axis = mode === "light" ? AXIS_LIGHT : AXIS_DARK;
  return {
    color:
      mode === "light"
        ? ["#0f9b84", "#35a2b8", "#e0a94e", "#7c8cff", "#c05a9a", "#4e9e8e", "#b3703c", "#64748b", "#3aa8c9", "#9a7fd8"]
        : ["#4da3ff", "#22d3ee", "#e8b45a", "#ff5464", "#00c48c", "#7c8cff", "#d873b8", "#3fbfbf", "#e8a26a", "#8ca0b3"],
    textStyle: { color: axis.color, fontFamily: "Segoe UI, Microsoft YaHei, system-ui, sans-serif" },
    legend: { textStyle: { color: axis.color } },
    categoryAxis: {
      axisLine: { lineStyle: { color: mode === "light" ? "#c2d3c9" : "#232c3b" } },
      axisTick: { lineStyle: { color: mode === "light" ? "#c2d3c9" : "#232c3b" } },
      axisLabel: { color: axis.color },
      splitLine: { show: false },
    },
    valueAxis: {
      axisLine: { show: false },
      axisLabel: { color: axis.color },
      splitLine: { lineStyle: { color: mode === "light" ? "rgba(31,46,40,0.1)" : "rgba(38,49,64,0.55)" } },
    },
    tooltip: {
      backgroundColor: mode === "light" ? "rgba(255,255,255,0.98)" : "rgba(17,22,31,0.96)",
      borderColor: mode === "light" ? "#dbe5df" : "#232c3b",
      textStyle: { color: mode === "light" ? "#1f2e28" : "#dde4ee" },
    },
  };
}

echarts.registerTheme("wl-light", baseTheme("light"));
echarts.registerTheme("wl-dark", baseTheme("dark"));

export default echarts;
export type { EChartsCoreOption } from "echarts/core";
