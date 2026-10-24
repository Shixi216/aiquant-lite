import { onBeforeUnmount, onMounted, watch, type Ref } from "vue";

import echarts, { type EChartsCoreOption } from "../charts";
import { useThemeStore } from "../stores/theme";

type ChartInstance = ReturnType<typeof echarts.init>;

export function useECharts(el: Ref<HTMLElement | null>) {
  const themeStore = useThemeStore();
  let chart: ChartInstance | null = null;
  let observer: ResizeObserver | null = null;
  let pendingOption: EChartsCoreOption | null = null;
  let pendingReplace = false;
  let lastOption: EChartsCoreOption | null = null;
  let lastReplace = false;
  /** 当前实例使用的主题名；主题变化时据此判断是否需要重建 */
  let builtWithTheme = "";

  function currentThemeName(): string {
    return themeStore.isDark ? "wl-dark" : "wl-light";
  }

  function getChart(): ChartInstance | null {
    if (!el.value) return null;
    const themeName = currentThemeName();
    if (!chart || builtWithTheme !== themeName) {
      if (chart) {
        observer?.disconnect();
        chart.dispose();
        chart = null;
      }
      chart = echarts.init(el.value, themeName);
      builtWithTheme = themeName;
      observer = new ResizeObserver(() => chart?.resize());
      observer.observe(el.value);
      const initial = lastOption ?? pendingOption;
      if (initial) {
        chart.setOption(initial, lastReplace || pendingReplace);
        pendingOption = null;
      }
    }
    return chart;
  }

  function setOption(option: EChartsCoreOption, replace = false) {
    // 防御：数据未就绪时调用方可能传入 null/undefined，直接忽略，避免 ECharts 抛错。
    if (option === null || option === undefined) return;
    lastOption = option;
    lastReplace = replace;
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
      builtWithTheme = "";
    }
    if (value) getChart();
  });

  // 主题/色盘切换：销毁重建（ECharts 不支持运行时换主题），并用最后一份 option 恢复内容
  watch(
    () => themeStore.chartVersion,
    () => {
      if (!el.value) return;
      getChart();
    },
  );

  onBeforeUnmount(() => {
    observer?.disconnect();
    chart?.dispose();
    chart = null;
  });

  return { setOption };
}
