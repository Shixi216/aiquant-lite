# -*- coding: utf-8 -*-
"""模型分析质量评估：对照人工标注计算五项指标（离线，无 LLM 调用）。

用法：python scripts/wealth_sentiment/eval_quality.py
输入：model_outputs_for_labeling.json + labels_20261025.json
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent

outputs = json.load(open(HERE / "model_outputs_for_labeling.json", encoding="utf-8"))
labels = json.load(open(HERE / "labels_20261025.json", encoding="utf-8"))["labels"]

valid = [(i, o) for i, o in enumerate(outputs)
         if o["status"] == "VALID" and str(i) in labels]
insufficient = [(i, o) for i, o in enumerate(outputs)
                if o["status"] == "INSUFFICIENT_DATA" and str(i) in labels]

# 1) 情绪方向准确率（VALID）
dir_ok = sum(1 for i, o in valid if labels[str(i)]["direction"] == o["sentiment_direction"])
# 2) 资产关联准确率（VALID：内容恰当；INSUFFICIENT：应为空）
sym_ok_valid = sum(1 for i, o in valid if labels[str(i)]["symbols_ok"])
sym_ok_insuf = sum(1 for i, o in insufficient if not o["affected_symbols"])
# 3) 事件类别准确率（严格：行情/公告应给具体类型）
type_ok = sum(1 for i, o in valid if labels[str(i)]["event_type_ok"])
# 4) 证据引用准确率（内容支撑判断；逐字性已由机器回文校验保证）
ev_ok = sum(1 for i, o in valid if labels[str(i)]["evidence_ok"])

print(f"样本：VALID {len(valid)} 例 + INSUFFICIENT {len(insufficient)} 例 = {len(valid)+len(insufficient)}")
print(f"1) 情绪方向准确率      {dir_ok}/{len(valid)} = {dir_ok/len(valid):.1%}")
print(f"2) 资产关联准确率      VALID {sym_ok_valid}/{len(valid)} = {sym_ok_valid/len(valid):.1%}"
      f" | 拒答应空符号 {sym_ok_insuf}/{len(insufficient)} = {sym_ok_insuf/len(insufficient):.1%}")
print(f"3) 事件类别准确率(严格) {type_ok}/{len(valid)} = {type_ok/len(valid):.1%}"
      "（OTHER 泛化已由 v1.1 确定性细化规则补偿，见 tests）")
print(f"4) 证据引用准确率      {ev_ok}/{len(valid)} = {ev_ok/len(valid):.1%}（逐字性机器校验另计）")

# 5) 不确定性校准：置信度分桶 vs 方向正确率 + 拒答正确率
print("\n5) 置信度校准（VALID：分桶方向准确率）")
buckets = [((0.0, 0.6), "<0.6"), ((0.6, 0.7), "0.6-0.7"), ((0.7, 1.01), ">=0.7")]
for (lo, hi), name in buckets:
    members = [(i, o) for i, o in valid if lo <= o["confidence"] < hi]
    if not members:
        continue
    ok = sum(1 for i, o in members if labels[str(i)]["direction"] == o["sentiment_direction"])
    print(f"   conf {name:8s}: n={len(members):2d} 方向正确 {ok}/{len(members)} = {ok/len(members):.0%}")
refusal_ok = sum(1 for i, o in insufficient
                 if labels[str(i)]["direction"] == "INSUFFICIENT_DATA")
print(f"   拒答正确率（INSUFFICIENT）: {refusal_ok}/{len(insufficient)} = {refusal_ok/len(insufficient):.0%}")

print("\n标注反馈已落地的规则（tests/test_wealth_sentiment_v11.py 覆盖）：")
print("  R1 拒答清空 affected_symbols + 置信度封顶 0.5（#14/#46/#16）")
print("  R2 event_type=OTHER 时按类别确定性细化（24 例类型泛化）")
print("  R3 主题规则：金龙→513050、欧元区/DAX→513030、石油→162411（#6/#11/#30）")
print("  R4 池外产品新闻 SAME_INDEX 低权重 0.35（#42 159655 案例）")
