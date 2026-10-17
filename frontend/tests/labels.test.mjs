import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/domain/labels.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const labels = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("centered production scores map to the displayed 0-100 scale", () => {
  assert.equal(labels.formatScore(-1), "0.0");
  assert.equal(labels.formatScore(0), "50.0");
  assert.equal(labels.formatScore(1), "100.0");
  assert.equal(labels.formatScore(-0.24), "38.0");
});
test("centered ranks map back to percentiles", () => {
  assert.equal(labels.formatRank(-0.24), "38百分位");
  assert.equal(labels.formatRank(0), "50百分位");
  assert.equal(labels.formatRankTop(0.8), "市场前10%");
});
test("canonical shares are displayed as lots", () => {
  assert.equal(labels.formatVolume(123450), "1235手");
  assert.equal(labels.formatVolume(3380000), "3.38万手");
  assert.equal(labels.formatVolume(0), "0手");
});
test("amount remains CNY and zero remains valid", () => {
  assert.equal(labels.formatAmount(100000000), "1.00亿元");
  assert.equal(labels.formatPct(0), "0.00%");
});
test("missing and nonfinite values do not become numeric claims", () => {
  for (const value of [null, undefined, NaN, Infinity]) {
    for (const format of [labels.formatScore, labels.formatRank, labels.formatVolume, labels.formatAmount]) {
      assert.equal(format(value), "暂无数据");
    }
  }
});
