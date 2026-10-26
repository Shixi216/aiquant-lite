/**
 * 财富实验室 v2 语义层。
 * 标签统一收敛到 wealthLabLabels.ts（唯一中文来源），此处仅保留 v2 专属辅助函数并向下兼容导出。
 */

import type { AiReviewVerdict, OptimizationMethod, RiskVerdict, V2AssetCategory } from "../types/wealthLabV2";
import {
  AI_REVIEW_VERDICT_LABELS,
  OPTIMIZATION_METHOD_LABELS,
  RISK_CATEGORY_LABELS,
  RISK_VERDICT_CLS,
  RISK_VERDICT_LABELS,
  STRATEGY_PARAM_LABELS,
  V2_CATEGORY_LABELS,
} from "./wealthLabLabels";

export {
  AI_REVIEW_VERDICT_LABELS,
  OPTIMIZATION_METHOD_LABELS,
  RISK_CATEGORY_LABELS,
  RISK_VERDICT_CLS,
  RISK_VERDICT_LABELS,
  STRATEGY_PARAM_LABELS,
  V2_CATEGORY_LABELS,
};

export function strategyFamilyLabelOf(category: V2AssetCategory): string {
  return V2_CATEGORY_LABELS[category];
}

/** 策略家族前缀 → 展示分组（策略工厂侧栏）。 */
export const STRATEGY_FAMILY_LABELS: Record<string, string> = {
  S0: "静态配置",
  S1: "静态配置",
  S2: "动量轮动",
  S3: "动量轮动",
  S4: "风险均衡",
  S5: "趋势防御",
  S6: "优化器",
  S7: "优化器",
  S8: "优化器",
};

export function strategyFamily(strategyId: string): string {
  const prefix = strategyId.slice(0, 2);
  return STRATEGY_FAMILY_LABELS[prefix] ?? "其他";
}

export type { AiReviewVerdict, OptimizationMethod, RiskVerdict };
