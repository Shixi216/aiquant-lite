<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import GroupReturnsChart from "../components/sim/GroupReturnsChart.vue";
import SymbolSeriesModal from "../components/sim/SymbolSeriesModal.vue";
import { errorMessage, isRouterDown } from "../api/client";
import type { SnapshotRow } from "../api/simDaily";
import { usePolling } from "../composables/usePolling";
import { useSimTrackerStore } from "../stores/simTracker";
import { getTimepointLabel, getVetoStatusLabel } from "../domain/labels";

const store = useSimTrackerStore();
const loading = ref(false);
const error = ref<string | null>(null);
const groupFilter = ref<string | null>(null);
const drawerSymbol = ref<string | null>(null);

const groups = ["命中组", "临界组", "对照组"];

async function guarded(fn: () => Promise<void>) {
  loading.value = true;
  error.value = null;
  try {
    await fn();
  } catch (err) {
    error.value = isRouterDown(err) ? "Router(8765) 不可达" : errorMessage(err);
  } finally {
    loading.value = false;
  }
}

const rows = computed<SnapshotRow[]>(() => {
  const results = store.snapshot?.results ?? [];
  const filtered = groupFilter.value
    ? results.filter((row) => row.观察组 === groupFilter.value)
    : results;
  return [...filtered].sort((a, b) => (b.总评分 ?? -1) - (a.总评分 ?? -1));
});

function formatPct(value: number | undefined): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return `${value.toFixed(2)}%`;
}

function pctClass(value: number | undefined): string {
  if (value === undefined || value === null || value === 0) return "";
  return value > 0 ? "up" : "down";
}

function timepointTitle(key: string): string {
  const tp = store.inventory?.timepoints.find((item) => item.key === key);
  return tp ? `${tp.zh}（${tp.result_count} 行）` : key;
}

onMounted(() => guarded(() => store.init()));
usePolling(() => store.refreshCurrent(), 60_000, 300_000);
</script>

<template>
  <h1 class="page-title">盘中模拟追踪</h1>
  <p class="page-sub">
    sim_daily 每日 14 时点快照 · 冻结池候选的评分 / 动作 / 仓位建议随时间演变 ·
    <span class="up">红涨</span> <span class="down">绿跌</span>（北京时间）
  </p>

  <div class="toolbar">
    <select :value="store.selectedDate ?? ''" @change="guarded(() => store.selectDate(($event.target as HTMLSelectElement).value))">
      <option v-for="date in store.dates" :key="date" :value="date">{{ date }}</option>
    </select>
    <template v-for="tp in ['pre','bid','v1','v2','v3','c10','c1030','am','pm','pm14','pm1430','vfinal','close','review']" :key="tp">
      <button
        class="chip"
        :class="{ active: store.selectedTimepoint === tp, disabled: !store.availableTimepoints.includes(tp) }"
        :disabled="!store.availableTimepoints.includes(tp)"
        :title="timepointTitle(tp)"
        @click="guarded(() => store.selectTimepoint(tp))"
      >
        {{ getTimepointLabel(tp) }}
      </button>
    </template>
    <span style="flex: 1" />
    <button class="chip" :class="{ active: groupFilter === null }" @click="groupFilter = null">全部</button>
    <button v-for="group in groups" :key="group" class="chip" :class="{ active: groupFilter === group }" @click="groupFilter = group">
      {{ group }}
    </button>
  </div>

  <div v-if="error" class="error-box">{{ error }}</div>
  <div v-else-if="loading && !store.snapshot" class="loading">加载中…</div>
  <template v-else-if="store.snapshot">
    <div class="panel">
      <h3>
        {{ store.snapshot.timepoint_zh }} · {{ store.snapshot.generated_at?.slice(0, 19).replace("T", " ") }}
        · {{ rows.length }} 只
      </h3>
      <table class="data">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>观察组</th>
            <th>当前动作</th>
            <th class="num">总评分</th>
            <th>盘中强度</th>
            <th>波段质量</th>
            <th>仓位建议</th>
            <th class="num">参考价格</th>
            <th class="num">涨跌幅</th>
            <th>风控否决</th>
            <th>相比上次</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.symbol" @click="drawerSymbol = row.symbol">
            <td>{{ row.symbol }}</td>
            <td>{{ row.name }}</td>
            <td><span class="badge">{{ row.观察组 ?? "—" }}</span></td>
            <td>
              <span v-if="row.status === 'ERROR'" class="badge veto" :title="row.error">异常</span>
              <span v-else class="badge action">{{ row.当前动作 ?? "—" }}</span>
            </td>
            <td class="num">{{ row.总评分?.toFixed(3) ?? "—" }}</td>
            <td>{{ row.盘中强度 ?? "—" }}</td>
            <td>{{ row.波段质量 ?? "—" }}</td>
            <td>{{ row.仓位建议 ?? "—" }}</td>
            <td class="num">{{ row.参考价格 ?? "—" }}</td>
            <td class="num" :class="pctClass(row.后续表现?.涨跌幅)">
              {{ formatPct(row.后续表现?.涨跌幅) }}
            </td>
            <td>
              <span class="badge" :class="row.VETO状态 === 'NO_VETO' ? 'ok' : 'veto'">
                {{ getVetoStatusLabel(row.VETO状态) }}
              </span>
            </td>
            <td :title="row.变化原因">{{ row.相比上次变化 || "—" }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="grid-2">
      <GroupReturnsChart :rows="store.snapshot.results" />
      <div class="panel">
        <h3>冻结池（{{ store.inventory?.pool.length ?? 0 }} 只）</h3>
        <table class="data">
          <thead>
            <tr><th>代码</th><th>名称</th><th>观察组</th><th class="num">冻结价</th></tr>
          </thead>
          <tbody>
            <tr v-for="entry in store.inventory?.pool ?? []" :key="entry.symbol" @click="drawerSymbol = entry.symbol">
              <td>{{ entry.symbol }}</td>
              <td>{{ entry.name }}</td>
              <td><span class="badge">{{ entry.group }}</span></td>
              <td class="num">{{ entry.price }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </template>

  <SymbolSeriesModal
    v-if="drawerSymbol && store.selectedDate"
    :date="store.selectedDate"
    :symbol="drawerSymbol"
    @close="drawerSymbol = null"
  />
</template>
