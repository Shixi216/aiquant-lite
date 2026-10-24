/**
 * 财富实验室 v2 跨页面共享状态（pinia）。
 * 当前用途：策略工厂产生的最近一次真实回测编号，供 AI 研究与决策包自动关联，
 * 避免决策包在可关联时仍以空指标生成。
 */

import { defineStore } from "pinia";

export interface LastStrategyBacktest {
  backtestId: string;
  strategyId: string;
  strategyVersion: string;
  /** 生成该回测的资产池（core4-v1 = v1/v2 基线；v3 池回测为动态池 ID）。 */
  universeId: string;
}

export const useWealthLabV2Store = defineStore("wealthLabV2", {
  state: () => ({
    lastStrategyBacktest: null as LastStrategyBacktest | null,
  }),
  getters: {
    /** 决策包自动关联提示文案；null 表示无可关联回测。 */
    backtestLinkHint(state): string | null {
      if (!state.lastStrategyBacktest) return null;
      const item = state.lastStrategyBacktest;
      const pool = item.universeId === "core4-v1" ? "v1/v2 基线" : `资产池 ${item.universeId}`;
      return `已自动关联最近策略回测：${item.backtestId}（${item.strategyId} · ${pool}）`;
    },
  },
  actions: {
    recordStrategyBacktest(backtestId: string, strategyId: string, strategyVersion: string, universeId = "core4-v1"): void {
      this.lastStrategyBacktest = { backtestId, strategyId, strategyVersion, universeId };
    },
  },
});
