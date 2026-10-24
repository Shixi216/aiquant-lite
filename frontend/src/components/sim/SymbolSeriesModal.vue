<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";

import { errorMessage } from "../../api/client";
import { fetchSymbolSeries, type SymbolSeries } from "../../api/simDaily";
import { useECharts } from "../../composables/useECharts";

const props = defineProps<{ date: string; symbol: string }>();
defineEmits<{ close: [] }>();

const data = ref<SymbolSeries | null>(null);
const error = ref<string | null>(null);
const chartEl = ref<HTMLElement | null>(null);
const { setOption } = useECharts(chartEl);

function render(series: SymbolSeries) {
  const labels = series.series.map((point) => point.timepoint);
  setOption(
    {
      tooltip: {
        trigger: "axis",
        formatter: (params: Array<{ dataIndex: number; seriesName: string; value: number }>) => {
          const index = params[0]?.dataIndex ?? 0;
          const point = series.series[index];
          const lines = [
            `<b>${point.timepoint_zh}</b>（${point.generated_at?.slice(11, 19) ?? ""}）`,
            `动作：${point.action ?? "—"}`,
            `评分：${point.score ?? "—"}`,
            `价格：${point.current_price ?? point.price ?? "—"}`,
            `涨跌幅：${point.pct === null || point.pct === undefined ? "—" : `${point.pct.toFixed(2)}%`}`,
            `仓位建议：${point.position_advice ?? "—"}`,
          ];
          return lines.join("<br/>");
        },
      },
      legend: { data: ["总评分", "涨跌幅%"], textStyle: { color: "#8494a7" }, top: 0 },
      grid: { left: 48, right: 48, top: 36, bottom: 28 },
      xAxis: { type: "category", data: labels, axisLabel: { color: "#8494a7" } },
      yAxis: [
        { type: "value", min: 0, max: 1, axisLabel: { color: "#8494a7" }, name: "评分" },
        { type: "value", axisLabel: { color: "#8494a7", formatter: "{value}%" }, name: "涨跌幅" },
      ],
      series: [
        {
          name: "总评分",
          type: "line",
          data: series.series.map((point) => point.score),
          smooth: true,
          itemStyle: { color: "#4da3ff" },
        },
        {
          name: "涨跌幅%",
          type: "line",
          yAxisIndex: 1,
          data: series.series.map((point) =>
            point.pct === null || point.pct === undefined ? null : Number(point.pct.toFixed(2)),
          ),
          smooth: true,
          itemStyle: { color: "#e6a23c" },
        },
      ],
    },
    true,
  );
}

onMounted(async () => {
  try {
    data.value = await fetchSymbolSeries(props.date, props.symbol);
    await nextTick();
    render(data.value);
  } catch (err) {
    error.value = errorMessage(err);
  }
});
</script>

<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal">
      <div class="modal-head">
        <h3 style="margin: 0">
          {{ data?.name ?? symbol }}（{{ symbol }}）· {{ date }} 跨时点轨迹
        </h3>
        <button class="close-btn" @click="$emit('close')">✕</button>
      </div>
      <div v-if="error" class="error-box">{{ error }}</div>
      <template v-else-if="data">
        <div ref="chartEl" style="height: 320px" />
        <table class="data" style="margin-top: 12px">
          <thead>
            <tr>
              <th>时点</th>
              <th>动作</th>
              <th class="num">评分</th>
              <th class="num">当前价</th>
              <th class="num">涨跌幅</th>
              <th>仓位建议</th>
              <th>VETO</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="point in data.series" :key="point.timepoint">
              <td :title="point.timepoint_zh">{{ point.timepoint }}</td>
              <td>{{ point.action ?? "—" }}</td>
              <td class="num">{{ point.score ?? "—" }}</td>
              <td class="num">{{ point.current_price ?? point.price ?? "—" }}</td>
              <td
                class="num"
                :class="point.pct && point.pct > 0 ? 'up' : point.pct && point.pct < 0 ? 'down' : ''"
              >
                {{ point.pct === null || point.pct === undefined ? "—" : `${point.pct.toFixed(2)}%` }}
              </td>
              <td>{{ point.position_advice ?? "—" }}</td>
              <td>
                <span class="badge" :class="point.veto === 'NO_VETO' ? 'ok' : 'veto'">
                  {{ point.veto ?? "—" }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </template>
      <div v-else class="loading">加载中…</div>
    </div>
  </div>
</template>
