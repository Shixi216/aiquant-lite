<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { TransitionRow } from "../../api/capitalBehavior";
import { useECharts } from "../../composables/useECharts";

const props = defineProps<{ rows: TransitionRow[]; topN?: number }>();

const chartEl = ref<HTMLElement | null>(null);
const { setOption } = useECharts(chartEl);

const NODE_COLORS = [
  "#4da3ff",
  "#e6a23c",
  "#2ebd85",
  "#f04848",
  "#9a7fd8",
  "#3fbfbf",
  "#d873b8",
  "#8ca0b3",
];

function hasPath(adjacency: Map<string, Set<string>>, from: string, to: string): boolean {
  if (from === to) return true;
  const stack = [from];
  const seen = new Set<string>([from]);
  while (stack.length) {
    const current = stack.pop() as string;
    if (current === to) return true;
    for (const next of adjacency.get(current) ?? []) {
      if (!seen.has(next)) {
        seen.add(next);
        stack.push(next);
      }
    }
  }
  return false;
}

const dagEdges = computed(() => {
  const ranked = [...props.rows].sort((a, b) => b.transitions - a.transitions);
  const adjacency = new Map<string, Set<string>>();
  const edges: TransitionRow[] = [];
  let skipped = 0;
  const limit = props.topN ?? 25;
  for (const edge of ranked) {
    if (edges.length >= limit) break;
    if (hasPath(adjacency, edge.to_state, edge.from_state)) {
      skipped += 1;
      continue;
    }
    edges.push(edge);
    if (!adjacency.has(edge.from_state)) adjacency.set(edge.from_state, new Set());
    adjacency.get(edge.from_state)?.add(edge.to_state);
  }
  return { edges, skipped };
});

const option = computed(() => {
  const edges = dagEdges.value.edges;
  const nameSet = new Set<string>();
  for (const edge of edges) {
    nameSet.add(edge.from_state);
    nameSet.add(edge.to_state);
  }
  const nodes = [...nameSet].map((name, index) => ({
    name,
    itemStyle: { color: NODE_COLORS[index % NODE_COLORS.length] },
  }));
  const links = edges.map((edge) => ({
    source: edge.from_state,
    target: edge.to_state,
    value: edge.transitions,
    prob: edge.transition_probability,
  }));
  return {
    tooltip: {
      formatter: (params: { dataType: string; data: { source?: string; target?: string; value?: number; prob?: number } }) => {
        if (params.dataType === "edge" && params.data.source) {
          return [
            `<b>${params.data.source} → ${params.data.target}</b>`,
            `转移次数：${params.data.value}`,
            `转移概率：${((params.data.prob ?? 0) * 100).toFixed(2)}%`,
          ].join("<br/>");
        }
        return params.data.value !== undefined ? String(params.data.value) : "";
      },
    },
    series: [
      {
        type: "sankey",
        left: 12,
        right: 150,
        top: 12,
        bottom: 12,
        nodeWidth: 14,
        nodeGap: 10,
        data: nodes,
        links,
        label: { color: "#d8dee6", fontSize: 11 },
        lineStyle: { color: "gradient", curveness: 0.5, opacity: 0.35 },
        emphasis: { focus: "adjacency" },
      },
    ],
  };
});

watch(option, (value) => setOption(value, true), { immediate: true, deep: true });
</script>

<template>
  <div class="panel">
    <h3>
      Top {{ topN ?? 25 }} 状态转移流（按转移次数）
      <span v-if="dagEdges.skipped" style="font-weight: 400; font-size: 11px">
        · 已剔除 {{ dagEdges.skipped }} 条成环边（桑基图仅支持有向无环）
      </span>
    </h3>
    <div ref="chartEl" style="height: 560px" />
  </div>
</template>
