# -*- coding: utf-8 -*-
"""AI 研究页两组图表（真实实验与调用审计数据）。"""
path = "src/views/AiResearchView.vue"
src = open(path, encoding="utf-8").read()

# 1) imports
old = 'import { getResearchRoleLabel, getResearchStatusLabel } from "../domain/labels";'
assert old in src, "labels import"
src = src.replace(old, old + '''
import type { Ref } from "vue";
import echarts, { type EChartsCoreOption } from "../charts";
import { useECharts } from "../composables/useECharts";
import { useChartTheme, withAlpha } from "../domain/chartTheme";''', 1)

# 2) options（实验散点 + 调用耗时）
old = '''const modelCalls = computed(() => runRecord.value?.model_calls ?? []);'''
new = '''const modelCalls = computed(() => runRecord.value?.model_calls ?? []);

/* ---------------- 实验与调用可视化（真实数据） ---------------- */

const expScatterEl = ref<HTMLElement | null>(null);
const expScatterChart = useECharts(expScatterEl as Ref<HTMLElement | null>);
const latencyBarEl = ref<HTMLElement | null>(null);
const latencyBarChart = useECharts(latencyBarEl as Ref<HTMLElement | null>);
const ct = useChartTheme();

/** 实验风险收益散点：横轴年化波动率、纵轴年化收益（真实回测 metrics）。 */
const expScatterOption = computed<EChartsCoreOption | null>(() => {
  const list = experiments.value;
  if (list.length < 2) return null;
  return {
    animationDuration: 450,
    tooltip: {
      trigger: "item",
      backgroundColor: ct.value.tooltipBg,
      borderColor: ct.value.border,
      textStyle: { color: ct.value.tooltipText, fontSize: 12 },
      formatter: (p: { name: string; value: number[] }) =>
        `<b>${p.name}</b><br/>年化波动率 ${p.value[0]}%<br/>年化收益 ${p.value[1]}%<br/>夏普比率 ${p.value[2]}`,
    },
    grid: { left: 60, right: 24, top: 22, bottom: 40 },
    xAxis: {
      name: "年化波动率",
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { color: ct.value.dim, fontSize: 11 },
      axisLabel: { color: ct.value.dim, formatter: "{value}%" },
      splitLine: { lineStyle: { color: ct.value.grid } },
      scale: true,
    },
    yAxis: {
      name: "年化收益",
      nameTextStyle: { color: ct.value.dim, fontSize: 11 },
      axisLabel: { color: ct.value.dim, formatter: "{value}%" },
      splitLine: { lineStyle: { color: ct.value.grid } },
      scale: true,
    },
    series: [
      {
        type: "scatter",
        symbolSize: (value: number[]) => Math.max(18, Math.min(40, 16 + value[2] * 12)),
        label: { show: true, position: "top", color: ct.value.text, fontSize: 11, formatter: (p: { name: string }) => p.name },
        itemStyle: { color: withAlpha(ct.value.accent, 0.78), borderColor: ct.value.accent, borderWidth: 1 },
        data: list.map((experiment) => ({
          name: experiment.strategy_id,
          value: [
            Number(((experiment.metrics?.annual_volatility ?? 0) * 100).toFixed(2)),
            Number(((experiment.metrics?.annual_return ?? 0) * 100).toFixed(2)),
            Number((experiment.metrics?.sharpe_ratio ?? 0).toFixed(2)),
          ],
        })),
      },
    ],
  } satisfies EChartsCoreOption;
});

/** 模型调用耗时条形（真实审计 latency_ms，按角色+模型）。 */
const latencyBarOption = computed<EChartsCoreOption | null>(() => {
  const calls = modelCalls.value.filter((call) => typeof call.latency_ms === "number");
  if (!calls.length) return null;
  return {
    animationDuration: 450,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: ct.value.tooltipBg,
      borderColor: ct.value.border,
      textStyle: { color: ct.value.tooltipText, fontSize: 12 },
      valueFormatter: (v: number) => `${(v / 1000).toFixed(1)} 秒`,
    },
    grid: { left: 120, right: 40, top: 10, bottom: 24 },
    xAxis: {
      type: "value",
      axisLabel: { color: ct.value.dim, fontSize: 10, formatter: (v: number) => `${(v / 1000).toFixed(0)}s` },
      splitLine: { lineStyle: { color: ct.value.grid } },
    },
    yAxis: {
      type: "category",
      data: calls.map((call) => `${getResearchRoleLabel(call.agent_role)} · ${call.display_model_name}`),
      axisLabel: { color: ct.value.text, fontSize: 11 },
      axisLine: { lineStyle: { color: ct.value.axisLine } },
    },
    series: [
      {
        type: "bar",
        barWidth: 14,
        itemStyle: { borderRadius: [0, 5, 5, 0], color: withAlpha(ct.value.accent, 0.8) },
        data: calls.map((call) => Math.round(call.latency_ms as number)),
      },
    ],
  } satisfies EChartsCoreOption;
});

watchEffect(() => {
  const opt = expScatterOption.value;
  if (opt) expScatterChart.setOption(opt, true);
});
watchEffect(() => {
  const opt = latencyBarOption.value;
  if (opt) latencyBarChart.setOption(opt, true);
});'''
assert old in src, "modelCalls anchor"
src = src.replace(old, new, 1)

# 3) 模板：实验散点插在实验对比表后
old = '''      <div v-show="comparison.length" ref="barEl"'''
if old not in src:
    pass  # 该 anchor 属于 compare 视图；AI 页用下方锚
old = '''          </div>
        </section>

        <!-- 7. 风险核查 -->'''
new = '''          </div>
          <div class="viz-caption">
            <strong>实验风险收益分布</strong>
            <span>横轴年化波动率、纵轴年化收益，点大小为夏普比率（真实回测指标）</span>
          </div>
          <div v-show="experiments.length >= 2" ref="expScatterEl" class="viz exp-scatter-viz"></div>
        </section>

        <!-- 7. 风险核查 -->'''
assert old in src, "experiments template anchor"
src = src.replace(old, new, 1)

# 4) 调用耗时图插在审计表后
old = """                <td class="error-text">{{ call.error_reason ?? "&mdash;" }}</td>"""
# 用独特行锚定：审计表 error_reason 单元格行之后找到表格结束
import re as _re
m = _re.search(r"( {18}<td class="error-text">\{\{ call\.error_reason[^}]+\}\}</td>
 {16}</tr>
 {14}</tbody>
 {12}</table>
 {10}</div>)", src)
assert m, "audit table anchor"
block = m.group(1)
insert = block + """
        <div class="viz-caption">
          <strong>模型调用耗时</strong>
          <span>每次真实调用的端到端延迟（审计记录）</span>
        </div>
        <div ref="latencyBarEl" class="viz latency-viz"></div>"""
src = src.replace(block, insert, 1)

# 5) 样式
old = "@media (max-width: 1200px) {\n  .layout { grid-template-columns: 1fr; }\n  .steps { grid-template-columns: 1fr; }\n}"
new = '''.viz-caption { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin: 12px 0 4px; }
.viz-caption strong { font-size: 12.5px; }
.viz-caption span { font-size: 11px; color: var(--text-dim); }
.exp-scatter-viz { height: 300px; }
.latency-viz { height: 150px; }
@media (max-width: 1200px) {
  .layout { grid-template-columns: 1fr; }
  .steps { grid-template-columns: 1fr; }
}'''
assert old in src, "style anchor"
src = src.replace(old, new, 1)

open(path, "w", encoding="utf-8", newline="\n").write(src)
print("ai viz applied")
