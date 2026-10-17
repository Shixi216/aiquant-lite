import { onBeforeUnmount, onMounted, watch, type Ref } from "vue";

import echarts, { type EChartsCoreOption } from "../charts";

type ChartInstance = ReturnType<typeof echarts.init>;

export function useECharts(el: Ref<HTMLElement | null>) {
  let chart: ChartInstance | null = null;
  let observer: ResizeObserver | null = null;
  let pendingOption: EChartsCoreOption | null = null;
  let pendingReplace = false;

  function getChart(): ChartInstance | null {
    if (!el.value) return null;
    if (!chart) {
      chart = echarts.init(el.value);
      observer = new ResizeObserver(() => chart?.resize());
      observer.observe(el.value);
      if (pendingOption) {
        chart.setOption(pendingOption, pendingReplace);
        pendingOption = null;
      }
    }
    return chart;
  }

  function setOption(option: EChartsCoreOption, replace = false) {
    const instance = getChart();
    if (instance) {
      instance.setOption(option, replace);
    } else {
      pendingOption = option;
      pendingReplace = replace;
    }
  }

  onMounted(() => {
    getChart();
  });

  watch(el, (value) => {
    if (chart && chart.getDom() !== value) {
      observer?.disconnect();
      chart.dispose();
      chart = null;
    }
    if (value) getChart();
  });

  onBeforeUnmount(() => {
    observer?.disconnect();
    chart?.dispose();
    chart = null;
  });

  return { setOption };
}
