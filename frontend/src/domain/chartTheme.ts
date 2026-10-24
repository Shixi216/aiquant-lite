/**
 * 图表取色层：把当前主题（CSS 变量）转成 ECharts option 可用的 token 集合。
 * 在组件的 computed option 内调用 useChartTheme() 并取值，
 * 主题切换 → store.chartVersion 递增 → computed 重算 → setOption 自动重绘。
 */

import { computed, type ComputedRef } from "vue";

import { readCssVar } from "./theme";
import { useThemeStore } from "../stores/theme";

export interface ChartTokens {
  /** 序列分类色（10） */
  palette: string[];
  /** 涨/跌（中国市场惯例：红涨绿跌），随主题切换 */
  up: string;
  down: string;
  warn: string;
  accent: string;
  accent2: string;
  gold: string;
  text: string;
  dim: string;
  faint: string;
  border: string;
  /** 分隔线/网格线 */
  grid: string;
  axisLine: string;
  tooltipBg: string;
  tooltipText: string;
  /** 热力图 5 档色带（浅→深） */
  heat: [string, string, string, string, string];
  isDark: boolean;
}

/** hex → rgba：用于面积图渐变等需要透明度的场景（主题色派生，避免硬编码）。 */
export function withAlpha(hex: string, alpha: number): string {
  let h = hex.trim();
  if (h.startsWith("#")) h = h.slice(1);
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (h.length === 8) h = h.slice(0, 6);
  const n = Number.parseInt(h, 16);
  if (Number.isNaN(n)) return hex;
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

export function useChartTheme(): ComputedRef<ChartTokens> {
  const store = useThemeStore();
  return computed<ChartTokens>(() => {
    // 依赖 chartVersion：主题/主色/色盘变化时重算
    void store.chartVersion;
    const isDark = store.isDark;
    const accent = readCssVar("--accent") || (isDark ? "#4da3ff" : "#0f9b84");
    const accent2 = readCssVar("--accent-2") || (isDark ? "#22d3ee" : "#35a2b8");
    const num = (name: string, fallback: string) => readCssVar(name) || fallback;
    const dim = num("--text-dim", isDark ? "#8595aa" : "#5f7469");
    const text = num("--text", isDark ? "#dde4ee" : "#1f2e28");
    const border = num("--border", isDark ? "#232c3b" : "#dbe5df");
    return {
      palette: store.chartColors,
      up: num("--up", isDark ? "#ff5464" : "#d9414e"),
      down: num("--down", isDark ? "#00c48c" : "#159a72"),
      warn: num("--warn", isDark ? "#f5a623" : "#bd7c17"),
      accent,
      accent2,
      gold: num("--gold", isDark ? "#e8b45a" : "#a9812f"),
      text,
      dim,
      faint: num("--text-faint", isDark ? "#5f7086" : "#90a49a"),
      border,
      grid: isDark ? "rgba(38, 49, 64, 0.55)" : "rgba(31, 46, 40, 0.1)",
      axisLine: border,
      tooltipBg: isDark ? "rgba(17, 22, 31, 0.96)" : "rgba(255, 255, 255, 0.98)",
      tooltipText: text,
      heat: isDark
        ? ["#10161f", "#1c4d80", "#2f7ed8", "#7fc3ff", "#d8ecff"]
        : ["#eef5f1", "#bfe0d4", "#5db8a0", accent, "#0a5c4e"],
      isDark,
    };
  });
}
