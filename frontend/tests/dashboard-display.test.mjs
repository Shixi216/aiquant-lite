import { test } from "node:test";
import assert from "node:assert/strict";

const {
  acceptedMarketBreadth,
  displayableResearchFactor,
  marketCoverageLabel,
} = await import("../src/domain/dashboardDisplay.ts");

function breadth(overrides = {}) {
  return {
    completeness_status: "COMPLETE",
    coverage_ratio: 0.9980122876761836,
    valid_symbol_count: 5570,
    ...overrides,
  };
}

test("达到后端完整门槛的市场快照以覆盖率展示", () => {
  const data = breadth();
  assert.equal(acceptedMarketBreadth(data), true);
  assert.equal(marketCoverageLabel(data), "99.8% · 5570只");
});

test("低于 99% 的市场样本不进入比赛展示", () => {
  assert.equal(acceptedMarketBreadth(breadth({ coverage_ratio: 0.989 })), false);
  assert.equal(acceptedMarketBreadth(breadth({ completeness_status: "PARTIAL" })), false);
});

test("低置信度研究因子隐藏", () => {
  assert.equal(displayableResearchFactor({ score: -0.7, confidence: 0.2, data_cutoff: "2026-10-24T17:03:00+08:00" }), null);
});

test("高置信度且结构完整的研究因子可以展示", () => {
  assert.deepEqual(
    displayableResearchFactor({ score: 0.42, confidence: 0.8, data_cutoff: "2026-10-26T16:00:00+08:00", formal_status: "shadow" }),
    { score: 0.42, confidence: 0.8, data_cutoff: "2026-10-26T16:00:00+08:00", formal_status: "shadow" },
  );
});
