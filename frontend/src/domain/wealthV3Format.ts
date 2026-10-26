/**
 * 财富实验室 v3 语义层：八大研究类别归并、数据质量状态、双数据源来源解析。
 * 全部中文标签沿用 domain/wealthLabLabels.ts 的统一映射精神，本文件只放 v3 专属内容。
 */

import type { AssetQuality, QualityStatus } from "../types/wealthLabV3";

/** 八大研究类别（任务书口径）：key 为归并组，value 为展示信息。 */
export interface ResearchCategory {
  key: string;
  label: string;
  /** 该组主要风险提示（展示口径，非后端字段）。 */
  risk: string;
  /** 契约 category 值 → 归并组。 */
  matches: (category: string) => boolean;
}

export const RESEARCH_CATEGORIES: ResearchCategory[] = [
  {
    key: "broad_equity",
    label: "宽基权益 ETF",
    risk: "系统性市场风险；风格切换时与基准同步波动",
    matches: (c) => c === "CN_EQUITY_BROAD_ETF" || c === "CN_EQUITY_ETF",
  },
  {
    key: "industry",
    label: "行业主题 ETF",
    risk: "行业集中度高；主题轮动与政策变化带来大幅回撤",
    matches: (c) => c === "CN_INDUSTRY_ETF" || c === "CN_THEME_ETF",
  },
  {
    key: "style",
    label: "风格因子 ETF",
    risk: "因子失效风险；风格拥挤时长期跑输基准",
    matches: (c) => c === "CN_STYLE_ETF" || c === "CN_FACTOR_ETF",
  },
  {
    key: "bond",
    label: "固定收益 ETF",
    risk: "利率风险；长久期品种对利率上行敏感",
    matches: (c) => c === "CN_BOND_ETF",
  },
  {
    key: "commodity",
    label: "黄金及商品",
    risk: "商品价格与人民币汇率双重波动",
    matches: (c) => c === "CN_COMMODITY_ETF" || c === "CN_GOLD_ETF" || c === "CN_GOLD_ETF_" || c.includes("GOLD") || c.includes("COMMODITY"),
  },
  {
    key: "convertible",
    label: "可转债",
    risk: "转股溢价回落与信用风险",
    matches: (c) => c === "CN_CONVERTIBLE_ETF" || c.includes("CONVERTIBLE") || c.includes("CB"),
  },
  {
    key: "qdii",
    label: "跨境 QDII",
    risk: "汇率、时差与额度限制；净值滞后于海外市场",
    matches: (c) => c.includes("QDII") || c.includes("CROSS_BORDER"),
  },
  {
    key: "reits",
    label: "公募 REITs",
    risk: "流动性不足；底层资产经营波动",
    matches: (c) => c.includes("REIT"),
  },
];

/** 未归入八类的类别原样归入"其他"，避免数据被吞掉。 */
export function categoryToGroup(category: string): ResearchCategory {
  return RESEARCH_CATEGORIES.find((item) => item.matches(category)) ?? {
    key: "other",
    label: `其他（${category}）`,
    risk: "待明确类别定义",
    matches: () => false,
  };
}

export const QUALITY_STATUS_LABELS: Record<QualityStatus, string> = {
  QUALIFIED: "数据合格",
  DEGRADED: "数据降级",
  REJECTED: "质量拒绝",
};

export const QUALITY_STATUS_CLS: Record<QualityStatus, string> = {
  QUALIFIED: "badge ok",
  DEGRADED: "badge warn",
  REJECTED: "badge veto",
};

/** 回测资格展示（以后端 quality_status 为准，不做前端推断）。 */
export function backtestEligibilityText(status: QualityStatus): { text: string; cls: string } {
  if (status === "QUALIFIED") return { text: "符合当前回测条件", cls: "up" };
  if (status === "DEGRADED") return { text: "条件受限（数据降级）", cls: "" };
  return { text: "不可回测（质量拒绝）", cls: "down" };
}

export function exchangeLabel(exchange: string): string {
  if (exchange === "SSE") return "上交所";
  if (exchange === "SZSE") return "深交所";
  return exchange;
}

/**
 * 解析 source_breakdown：契约声明 object，但后端实际返回 JSON 字符串。
 * 返回 [{ source, rows }]，并给出供应商性质标签（官方/代理）。
 */
export function parseSourceBreakdown(value: object | string | null | undefined): { source: string; sourceLabel: string; rows: number; official: boolean }[] {
  let parsed: Record<string, number> = {};
  if (typeof value === "string") {
    try {
      parsed = JSON.parse(value) as Record<string, number>;
    } catch {
      parsed = {};
    }
  } else if (value && typeof value === "object") {
    parsed = value as Record<string, number>;
  }
  return Object.entries(parsed).map(([source, rows]) => {
    const official = source === "OFFICIAL";
    return {
      source,
      sourceLabel: official ? "官方 Tushare" : "代理源（PROXY）",
      rows: Number(rows) || 0,
      official,
    };
  });
}

/** 数据完整率展示：1 - missing_ratio。 */
export function completeness(missingRatio: number): string {
  const ratio = Math.max(0, Math.min(1, 1 - missingRatio));
  return `${(ratio * 100).toFixed(1)}%`;
}

/* ----- v3.1 分类维度中文标签（未登记值回退原码，不编造） ----- */

export const PRODUCT_FORM_LABELS: Record<string, string> = {
  ETF: "ETF",
  LOF: "LOF",
};

export const MARKET_SCOPE_LABELS: Record<string, string> = {
  DOMESTIC: "境内",
  HK_CONNECT: "港股通",
  OVERSEAS: "境外市场",
};

export const CURRENCY_RISK_LABELS: Record<string, string> = {
  NONE: "无",
  CNY: "人民币",
  HKD: "港元",
  USD: "美元",
  USD_MULTI: "美元多资产",
};

export function productFormLabel(value: string | null | undefined): string {
  if (!value) return "";
  return PRODUCT_FORM_LABELS[value] ?? value;
}

export function marketScopeLabel(value: string | null | undefined): string {
  if (!value) return "";
  return MARKET_SCOPE_LABELS[value] ?? value;
}

export function currencyRiskLabel(value: string | null | undefined): string {
  if (!value) return "";
  return CURRENCY_RISK_LABELS[value] ?? value;
}

/** 类别组统计（全部来自接口资产记录，前端不编造数量）。 */
export interface CategoryStat {
  key: string;
  label: string;
  risk: string;
  total: number;
  qualified: number;
  degraded: number;
  rejected: number;
  coverageStart: string | null;
  coverageEnd: string | null;
  avgMissingRatio: number | null;
  connected: boolean;
}

export function buildCategoryStats(assets: AssetQuality[]): CategoryStat[] {
  return RESEARCH_CATEGORIES.map((category) => {
    const group = assets.filter((asset) => category.matches(asset.category));
    if (!group.length) {
      return { key: category.key, label: category.label, risk: category.risk, total: 0, qualified: 0, degraded: 0, rejected: 0, coverageStart: null, coverageEnd: null, avgMissingRatio: null, connected: false };
    }
    const starts = group.map((asset) => asset.data_first).filter((v): v is string => !!v);
    const ends = group.map((asset) => asset.data_last).filter((v): v is string => !!v);
    return {
      key: category.key,
      label: category.label,
      risk: category.risk,
      total: group.length,
      qualified: group.filter((asset) => asset.quality_status === "QUALIFIED").length,
      degraded: group.filter((asset) => asset.quality_status === "DEGRADED").length,
      rejected: group.filter((asset) => asset.quality_status === "REJECTED").length,
      coverageStart: starts.length ? starts.reduce((a, b) => (a < b ? a : b)) : null,
      coverageEnd: ends.length ? ends.reduce((a, b) => (a > b ? a : b)) : null,
      avgMissingRatio: group.reduce((acc, asset) => acc + asset.missing_ratio, 0) / group.length,
      connected: true,
    };
  });
}
