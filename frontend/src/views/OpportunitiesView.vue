<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { fetchOpportunities } from "../api/v2";
import { errorMessage } from "../api/client";
import { usePolling } from "../composables/usePolling";
import type { OpportunitiesResponse } from "../types/v2";
import OpportunityTable from "../components/opportunities/OpportunityTable.vue";
import BlockState from "../components/global/BlockState.vue";
import ProvenanceLine from "../components/global/ProvenanceLine.vue";
import { getBlockReasonLabel, formatDate } from "../domain/labels";

const data = ref<OpportunitiesResponse | null>(null);
const error = ref<string | null>(null);
const minScore = ref<number | undefined>(undefined);
const sort = ref("composite");
const offset = ref(0);
const pageSize = 50;
function resetFilters() { offset.value = 0; load(); }
function changePage(delta: number) { offset.value = Math.max(0, offset.value + delta * pageSize); load(); }

const loading = ref(false);
let latestRequest = 0;

async function load() {
  const request = ++latestRequest;
  loading.value = true;
  try {
    const result = await fetchOpportunities({
      limit: pageSize,
      offset: offset.value,
      min_score: typeof minScore.value === "number" && Number.isFinite(minScore.value) ? minScore.value / 50 - 1 : undefined,
      sort: sort.value,
    });
    if (request !== latestRequest) return;
    data.value = result;
    error.value = null;
  } catch (err) {
    if (request === latestRequest) error.value = errorMessage(err);
  } finally {
    if (request === latestRequest) loading.value = false;
  }
}

onMounted(load);
watch([sort, minScore], resetFilters);
usePolling(load, 5 * 60 * 1000, 5 * 60 * 1000);
</script>

<template>
  <div class="opportunities-view">
    <div class="view-actions"><span>按市场截面比较研究机会</span><button class="mini" :disabled="loading" @click="load">{{ loading ? '更新中…' : '刷新数据' }}</button></div>
    <div v-if="data?.fixture_mode" class="fixture-banner" role="status">演示样本 · 当前内容仅用于界面验证</div>
    <div v-if="loading && !data" class="loading" role="status">正在读取机会排序…</div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <template v-if="data">
      <div class="toolbar">
        <label>
          排序
          <select v-model="sort">
            <option value="composite">综合</option>
            <option value="tech">技术</option>
            <option value="fund">基本面</option>
            <option value="sent">情绪</option>
            <option value="margin">两融</option>
          </select>
        </label>
        <label>
          最低综合分（0—100）
          <input
            v-model.number="minScore"
            type="number"
            step="5"
            min="0"
            max="100"
            placeholder="不限"
          />
        </label>
        <span v-if="data.ranking_data_cutoff" class="cutoff">
          排名数据截至 {{ formatDate(data.ranking_data_cutoff) }}
          <span class="dim">（排名与报价可能来自不同日期）</span>
        </span>
      </div>
      <p class="research-note">横截面候选用于研究排序；证券上市状态、停牌状态和详细证据请进入个股页核验。</p>
      <BlockState :block="data.cross_section_block">
        <template #fallback>
          <p class="dim">
            {{ getBlockReasonLabel(data.cross_section_block.reason_code) }}
          </p>
        </template>
        <OpportunityTable
          :items="data.items"
          :classification-source="data.classification_source"
        />
      </BlockState>
      <div v-if="data.cross_section_block.available" class="pagination">
        <span>共 {{ data.total }} 只 · 当前显示 {{ data.items.length ? data.offset + 1 : 0 }}—{{ data.offset + data.items.length }}</span>
        <div><button class="mini" :disabled="loading || data.offset === 0" @click="changePage(-1)">上一页</button> <button class="mini" :disabled="loading || data.offset + data.limit >= data.total" @click="changePage(1)">下一页</button></div>
      </div>
      <ProvenanceLine
        :source="data.provenance.data_source"
        :data-time="data.ranking_data_cutoff"
        :formal-status="data.provenance.formal_status"
      />
    </template>
  </div>
</template>

<style scoped>
.pagination { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin: 16px 0; color: var(--text-dim); font-size: 12px; }
.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 14px;
  font-size: 13px;
}
.toolbar select,
.toolbar input {
  background: var(--bg-panel);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 8px;
}
.cutoff {
  color: var(--warn);
}
.dim {
  color: var(--text-dim);
}
.research-note {
  margin: -4px 0 12px;
  color: var(--text-dim);
  font-size: 12px;
}
.error-banner {
  background: var(--bg-panel);
  border: 1px solid var(--veto);
  color: var(--veto);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 16px;
}
</style>
