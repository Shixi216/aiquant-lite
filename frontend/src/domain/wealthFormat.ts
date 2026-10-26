/**
 * 财富实验室专用语义层：资产展示元数据与数字格式化。
 * 契约约定：收益率/权重/比例一律小数（0.023 = 2.3%）；金额为人民币元。
 */

import type { AssetCategory } from "../types/wealthLab";

export const ASSET_CATEGORY_LABELS: Record<AssetCategory, string> = {
  EQUITY_ETF: "权益 ETF",
  BOND_ETF: "债券 ETF",
  GOLD_ETF: "黄金 ETF",
  CONVERTIBLE_ETF: "可转债 ETF",
};

/** 四资产固定主题色（与全站暗色金融风格一致）。 */
export const ASSET_COLORS: Record<AssetCategory, string> = {
  EQUITY_ETF: "#4da3ff",
  BOND_ETF: "#22d3ee",
  GOLD_ETF: "#e8b45a",
  CONVERTIBLE_ETF: "#b58cff",
};

/** 契约缺省权重（BacktestRequest 注释）：40/40/10/10。assets 接口可用时以其 default_weight 为准。 */
export const DEFAULT_WEIGHTS: Record<string, number> = {
  "510300.SH": 0.4,
  "511010.SH": 0.4,
  "518880.SH": 0.1,
  "511380.SH": 0.1,
};

export const DEFAULT_ASSET_ORDER = ["510300.SH", "511010.SH", "518880.SH", "511380.SH"] as const;

export const REBALANCE_LABELS: Record<string, string> = {
  monthly: "月度再平衡",
  quarterly: "季度再平衡",
  none: "不再平衡",
};

/** 允许的权重求和误差（避免浮点误差把 100% 判为非法）。 */
export const WEIGHT_SUM_EPSILON = 1e-6;

export function weightSum(weights: Record<string, number>): number {
  return Object.values(weights).reduce((acc, value) => acc + (Number.isFinite(value) ? value : 0), 0);
}

export function isWeightsValid(weights: Record<string, number>): boolean {
  if (!Object.keys(weights).length) return false;
  for (const value of Object.values(weights)) {
    if (!Number.isFinite(value) || value < 0 || value > 1) return false;
  }
  return Math.abs(weightSum(weights) - 1) <= WEIGHT_SUM_EPSILON;
}

/** 宽松入参：v1 Asset[] 与 v2 UniverseAsset[] 均满足 { symbol, name }。 */
export function assetName(assets: { symbol: string; name: string }[] | null, symbol: string): string {
  return assets?.find((asset) => asset.symbol === symbol)?.name ?? symbol;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** 小数 → 百分比字符串；signed=true 时带 +/- 符号。 */
export function fmtPct(value: number | null | undefined, digits = 2, signed = true): string {
  if (!finite(value)) return "—";
  const text = `${(value * 100).toFixed(digits)}%`;
  if (!signed || value <= 0) return value === 0 ? "0.00%".replace("0.00", (0).toFixed(digits)) : text;
  return `+${text}`;
}

/** 净值（4 位小数，与契约净值精度一致）。 */
export function fmtNav(value: number | null | undefined): string {
  return finite(value) ? value.toFixed(4) : "—";
}

/** 比率类指标（夏普/卡玛），2 位小数。 */
export function fmtRatio(value: number | null | undefined, digits = 2): string {
  return finite(value) ? value.toFixed(digits) : "—";
}

/** 人民币金额，千分位；量级大时自动降单位（万/亿）。 */
export function fmtCny(value: number | null | undefined, digits = 0): string {
  if (!finite(value)) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e8) return `${sign}¥${(abs / 1e8).toFixed(2)} 亿`;
  if (abs >= 1e4) return `${sign}¥${(abs / 1e4).toFixed(2)} 万`;
  return `${sign}¥${abs.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits })}`;
}

/** 由小数收益率换算盈亏颜色类：A股红涨绿跌（与全站 --up/--down 一致）。 */
export function returnColorClass(value: number | null | undefined): string {
  if (!finite(value) || value === 0) return "";
  return value > 0 ? "up" : "down";
}
