import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, HeatmapChart, LineChart, SankeyChart } from "echarts/charts";
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

export default echarts;
export type { EChartsCoreOption } from "echarts/core";
