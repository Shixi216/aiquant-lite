/**
 * 主题系统纯函数层（不依赖 Vue/Pinia，可被 main.ts 在挂载前调用，避免首屏闪烁）。
 * 持久化仅存外观偏好（localStorage），不涉及任何金融数据或凭据。
 */

export type ThemePresetId = "mist-jade" | "cloud-blue" | "cream-apricot" | "polar-night";
export type ThemeMode = "light" | "dark";
export type Density = "compact" | "cozy" | "comfortable";
export type NavLayout = "sidebar" | "top";
export type ShadowLevel = 0 | 1 | 2;

export interface ThemePresetMeta {
  id: ThemePresetId;
  name: string;
  description: string;
  /** 预设卡片预览色 */
  swatch: [string, string, string];
  defaultMode: ThemeMode;
}

export const THEME_PRESETS: ThemePresetMeta[] = [
  {
    id: "mist-jade",
    name: "晨雾青绿",
    description: "珍珠白背景 · 薄荷卡片 · 青绿主色（默认，清新专业）",
    swatch: ["#f3f7f5", "#0f9b84", "#eef7f2"],
    defaultMode: "light",
  },
  {
    id: "cloud-blue",
    name: "云朵浅蓝",
    description: "柔和浅灰蓝 · 蔚蓝主色（清爽冷静）",
    swatch: ["#f2f6fb", "#2f7ed8", "#eef4fb"],
    defaultMode: "light",
  },
  {
    id: "cream-apricot",
    name: "奶油杏金",
    description: "米白暖底 · 杏金主色（温润亲和）",
    swatch: ["#f9f5ee", "#b97f24", "#f8f1e3"],
    defaultMode: "light",
  },
  {
    id: "polar-night",
    name: "极夜专业版",
    description: "原有深色量化终端（完整保留，可随时切回）",
    swatch: ["#0a0e14", "#4da3ff", "#151b26"],
    defaultMode: "dark",
  },
];

/** ECharts 色盘预设；"default" 表示跟随当前主题主色自动派生。 */
export interface ChartPaletteMeta {
  id: string;
  name: string;
  colors: string[] | null;
}

export const CHART_PALETTES: ChartPaletteMeta[] = [
  { id: "default", name: "跟随主题主色", colors: null },
  {
    id: "verdant",
    name: "青绿商务",
    colors: ["#0f9b84", "#35a2b8", "#e0a94e", "#7c8cff", "#c05a9a", "#4e9e8e", "#b3703c", "#64748b", "#3aa8c9", "#9a7fd8"],
  },
  {
    id: "azure",
    name: "蔚蓝金融",
    colors: ["#2f7ed8", "#38a3c9", "#0f9b84", "#e0a94e", "#7c8cff", "#c05a9a", "#64748b", "#4e9e8e", "#b3703c", "#3fbfbf"],
  },
  {
    id: "neon",
    name: "极夜霓虹（经典）",
    colors: ["#4da3ff", "#22d3ee", "#e8b45a", "#ff5464", "#00c48c", "#7c8cff", "#d873b8", "#3fbfbf", "#e8a26a", "#8ca0b3"],
  },
];

/** 仪表盘组件注册表（显示/排列定制用；DashboardView 按 id 渲染）。 */
export const DASHBOARD_WIDGETS = [
  { id: "freshness", name: "数据新鲜度条" },
  { id: "components", name: "数据组件状态" },
  { id: "summary", name: "核心指标卡" },
  { id: "breadth", name: "涨跌家数图表" },
  { id: "context", name: "市场环境" },
  { id: "dragon-tiger", name: "龙虎榜" },
  { id: "risk", name: "风险中心" },
  { id: "provenance", name: "数据溯源" },
] as const;

export interface ThemeSettings {
  preset: ThemePresetId;
  mode: ThemeMode;
  /** null = 跟随预设主色 */
  primaryColor: string | null;
  secondaryColor: string | null;
  /** null = 跟随预设页面背景 */
  pageBackground: string | null;
  /** 卡片圆角 px（6–18） */
  radius: number;
  shadowLevel: ShadowLevel;
  /** 全局字号 px（13–16） */
  fontSize: number;
  density: Density;
  navLayout: NavLayout;
  sidebarWidth: number;
  sidebarCollapsed: boolean;
  chartPaletteId: string;
  /** 仪表盘组件顺序（id 列表） */
  dashboardOrder: string[];
  /** 隐藏的仪表盘组件 id */
  dashboardHidden: string[];
}

export const DEFAULT_SETTINGS: ThemeSettings = {
  preset: "mist-jade",
  mode: "light",
  primaryColor: null,
  secondaryColor: null,
  pageBackground: null,
  radius: 14,
  shadowLevel: 1,
  fontSize: 14,
  density: "cozy",
  navLayout: "sidebar",
  sidebarWidth: 232,
  sidebarCollapsed: false,
  chartPaletteId: "default",
  dashboardOrder: DASHBOARD_WIDGETS.map((w) => w.id),
  dashboardHidden: [],
};

const STORAGE_KEY = "wl-theme-settings-v1";

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** 宽松校验并补全缺省字段（导入/历史版本兼容）。 */
export function normalizeSettings(raw: unknown): ThemeSettings {
  const input = (raw ?? {}) as Partial<ThemeSettings>;
  const presetIds = THEME_PRESETS.map((p) => p.id);
  const preset = presetIds.includes(input.preset as ThemePresetId)
    ? (input.preset as ThemePresetId)
    : DEFAULT_SETTINGS.preset;
  const mode: ThemeMode = input.mode === "dark" ? "dark" : "light";
  const paletteIds = CHART_PALETTES.map((p) => p.id);
  const widgetIds = DASHBOARD_WIDGETS.map((w) => w.id);
  const order = Array.isArray(input.dashboardOrder)
    ? input.dashboardOrder.filter((id) => widgetIds.includes(id))
    : [];
  // 未注册的新组件 id 追加到末尾，避免升级丢组件
  for (const id of widgetIds) if (!order.includes(id)) order.push(id);
  return {
    preset,
    mode,
    primaryColor: typeof input.primaryColor === "string" ? input.primaryColor : null,
    secondaryColor: typeof input.secondaryColor === "string" ? input.secondaryColor : null,
    pageBackground: typeof input.pageBackground === "string" ? input.pageBackground : null,
    radius: clamp(Number(input.radius ?? DEFAULT_SETTINGS.radius), 4, 22),
    shadowLevel: (input.shadowLevel === 0 || input.shadowLevel === 2 ? input.shadowLevel : 1) as ShadowLevel,
    fontSize: clamp(Number(input.fontSize ?? DEFAULT_SETTINGS.fontSize), 12, 17),
    density: input.density === "compact" || input.density === "comfortable" ? input.density : "cozy",
    navLayout: input.navLayout === "top" ? "top" : "sidebar",
    sidebarWidth: clamp(Number(input.sidebarWidth ?? DEFAULT_SETTINGS.sidebarWidth), 190, 320),
    sidebarCollapsed: input.sidebarCollapsed === true,
    chartPaletteId: paletteIds.includes(input.chartPaletteId as string)
      ? (input.chartPaletteId as string)
      : "default",
    dashboardOrder: order,
    dashboardHidden: Array.isArray(input.dashboardHidden)
      ? input.dashboardHidden.filter((id) => widgetIds.includes(id))
      : [],
  };
}

export function loadSettings(): ThemeSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    return normalizeSettings(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(settings: ThemeSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    /* 隐私模式等场景下静默失败：设置不持久但不影响运行 */
  }
}

function hexOk(value: string): boolean {
  return /^#[0-9a-fA-F]{3,8}$/.test(value);
}

/**
 * 将设置落到 <html>：dataset 驱动 tokens.css 的预设选择，
 * 内联样式承载用户级覆盖（优先级高于预设）。
 */
export function applyTheme(settings: ThemeSettings): void {
  const root = document.documentElement;
  root.dataset.wlTheme = settings.preset;
  root.dataset.wlMode = settings.mode;
  root.dataset.wlDensity = settings.density;
  root.dataset.wlShadow = String(settings.shadowLevel);
  root.dataset.nav = settings.navLayout;
  root.dataset.navCollapse = settings.sidebarCollapsed ? "1" : "0";
  const style = root.style;
  if (settings.primaryColor && hexOk(settings.primaryColor)) style.setProperty("--accent", settings.primaryColor);
  else style.removeProperty("--accent");
  if (settings.secondaryColor && hexOk(settings.secondaryColor)) style.setProperty("--accent-2", settings.secondaryColor);
  else style.removeProperty("--accent-2");
  if (settings.pageBackground && hexOk(settings.pageBackground)) style.setProperty("--bg", settings.pageBackground);
  else style.removeProperty("--bg");
  style.setProperty("--radius-card", `${settings.radius}px`);
  style.setProperty("--font-base", `${settings.fontSize}px`);
  style.setProperty("--sidebar-width", settings.navLayout === "top" ? "100%" : `${settings.sidebarWidth}px`);
}

/** 图表取色：从当前生效的 CSS 变量读取（与实际渲染主题严格一致）。 */
export function readCssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
