/**
 * 主题全局状态（pinia）：设置读写、持久化、DOM 应用与图表重绘信号。
 * 任何影响 ECharts 取色的变化（模式/预设/主色/色盘）都会递增 chartVersion，
 * useECharts 据此重建实例，组件内 computed option 重新求值。
 */

import { defineStore } from "pinia";

import {
  applyTheme,
  CHART_PALETTES,
  DEFAULT_SETTINGS,
  loadSettings,
  normalizeSettings,
  saveSettings,
  THEME_PRESETS,
  type ThemeSettings,
} from "../domain/theme";

export const useThemeStore = defineStore("theme", {
  state: () => {
    const settings = loadSettings();
    return {
      settings,
      /** 影响 ECharts 的设置版本号（供 useECharts/图表组件 watch） */
      chartVersion: 0,
      /** 全部设置版本号（含布局类） */
      version: 0,
    };
  },
  getters: {
    presetMeta(state) {
      return THEME_PRESETS.find((p) => p.id === state.settings.preset) ?? THEME_PRESETS[0]!;
    },
    isDark(state): boolean {
      return state.settings.mode === "dark";
    },
    /** 图表序列色盘：default 跟随主题主色，其余为固定预设。 */
    chartColors(state): string[] {
      const meta = CHART_PALETTES.find((p) => p.id === state.settings.chartPaletteId);
      if (meta?.colors) return meta.colors;
      const accent = state.settings.primaryColor;
      const accent2 = state.settings.secondaryColor;
      const fixedLight = ["#e0a94e", "#7c8cff", "#c05a9a", "#4e9e8e", "#b3703c", "#8f9bb3", "#3aa8c9", "#9a7fd8"];
      const fixedDark = ["#e8b45a", "#7c8cff", "#d873b8", "#3fbfbf", "#e8a26a", "#8ca0b3", "#46c0dd", "#9a7fd8"];
      const fixed = state.settings.mode === "dark" ? fixedDark : fixedLight;
      const head = [accent, accent2].filter((c): c is string => typeof c === "string");
      return [...head, ...fixed].slice(0, 10);
    },
  },
  actions: {
    update(patch: Partial<ThemeSettings>): void {
      const next = normalizeSettings({ ...this.settings, ...patch });
      const chartAffecting =
        next.mode !== this.settings.mode ||
        next.preset !== this.settings.preset ||
        next.primaryColor !== this.settings.primaryColor ||
        next.secondaryColor !== this.settings.secondaryColor ||
        next.chartPaletteId !== this.settings.chartPaletteId ||
        next.pageBackground !== this.settings.pageBackground;
      this.settings = next;
      applyTheme(next);
      saveSettings(next);
      this.version += 1;
      if (chartAffecting) this.chartVersion += 1;
    },
    toggleMode(): void {
      this.update({ mode: this.settings.mode === "dark" ? "light" : "dark" });
    },
    selectPreset(presetId: ThemeSettings["preset"]): void {
      const meta = THEME_PRESETS.find((p) => p.id === presetId);
      this.update({ preset: presetId, mode: meta?.defaultMode ?? this.settings.mode });
    },
    toggleSidebar(): void {
      this.update({ sidebarCollapsed: !this.settings.sidebarCollapsed });
    },
    reorderDashboard(fromId: string, toId: string): void {
      const order = [...this.settings.dashboardOrder];
      const from = order.indexOf(fromId);
      const to = order.indexOf(toId);
      if (from < 0 || to < 0 || from === to) return;
      order.splice(to, 0, ...order.splice(from, 1));
      this.update({ dashboardOrder: order });
    },
    toggleDashboardWidget(id: string): void {
      const hidden = new Set(this.settings.dashboardHidden);
      if (hidden.has(id)) hidden.delete(id);
      else hidden.add(id);
      this.update({ dashboardHidden: [...hidden] });
    },
    resetAll(): void {
      this.update({ ...DEFAULT_SETTINGS });
    },
    exportSettings(): string {
      return JSON.stringify({ app: "aiquant-lite", kind: "wl-theme-settings", version: 1, settings: this.settings }, null, 2);
    },
    importSettings(json: string): void {
      const parsed = JSON.parse(json) as { settings?: unknown };
      const raw = parsed && typeof parsed === "object" && "settings" in parsed ? parsed.settings : parsed;
      this.update(normalizeSettings(raw));
    },
  },
});
