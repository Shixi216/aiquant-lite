<script setup lang="ts">
/**
 * 多资产市场情绪工作台（wealth-sentiment/v1.1，CURRENT_RESEARCH_ONLY）。
 * 契约：trading/wealth_lab/sentiment/CONTRACT.md。
 * 读端点直连情绪模块；三个写操作走受控 Jobs API（提交 → job_id → 真实轮询）。
 * 证据口径以后端为准：EXACT=直接证据；CLASS_LEVEL=间接推导（置信度封顶 0.6）；
 * INSUFFICIENT_DATA=证据不足（灰显，不得当作信号）。前端聚合视图均明确标注。
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { fetchV3Quality } from "../api/wealthLabV3";
import { fetchJob } from "../api/taskCenter";
import {
  fetchAssetNews,
  fetchAssetScore,
  fetchMarketHistory,
  fetchMarketSentiment,
  fetchNewsEvents,
  fetchScoreEvidence,
  fetchSources,
  submitNewsRefresh,
  submitSentimentAnalysis,
  submitSentimentRebuild,
  type NewsEventQuery,
} from "../api/sentiment";
import { errorMessage } from "../api/client";
import type { AssetNewsItem, NewsEvent, ScoreEvidenceDetail, SentimentJobSubmitData, SentimentScore } from "../types/sentiment";
import type { AssetQuality } from "../types/wealthLabV3";
import type { JobPublic } from "../api/taskCenter";

/* ---------------- 资产主数据（v3 研究池质量接口） ---------------- */

const assets = ref<AssetQuality[]>([]);
const assetsLoading = ref(false);

async function loadAssets() {
  assetsLoading.value = true;
  try {
    const result = await fetchV3Quality(undefined, 1, 200);
    assets.value = result.envelope.data.assets;
  } catch { /* 资产表缺失时情绪表仍可按 symbol 展示 */ }
  finally {
    assetsLoading.value = false;
  }
}

function assetName(symbol: string): string {
  return assets.value.find((a) => a.symbol === symbol)?.name ?? symbol;
}
function assetCategory(symbol: string): string {
  return assets.value.find((a) => a.symbol === symbol)?.category ?? "—";
}

/* ---------------- 市场总体情绪 ---------------- */

const marketScore = ref<SentimentScore | null>(null);
const marketInsufficient = ref(false);
const marketMessage = ref<string | null>(null);
const marketHistory = ref<{ asof: string; sentiment_score: number }[]>([]);
const marketLoading = ref(false);
const marketError = ref<string | null>(null);

async function loadMarket() {
  marketLoading.value = true;
  marketError.value = null;
  try {
    const data = await fetchMarketSentiment();
    marketScore.value = data.score;
    marketInsufficient.value = !data.score && data.evidence_basis === "INSUFFICIENT_DATA";
    marketMessage.value = data.message ?? null;
    const history = await fetchMarketHistory(30).catch(() => null);
    marketHistory.value = history?.history ?? [];
  } catch (err) {
    marketError.value = errorMessage(err);
  } finally {
    marketLoading.value = false;
  }
}

/* ---------------- 资产情绪（33 只，逐只后端评分） ---------------- */

const assetScores = ref<Record<string, SentimentScore | "INSUFFICIENT">>({});
const assetScoresLoading = ref(false);
const assetScoresError = ref<string | null>(null);

async function loadAssetScores() {
  if (!assets.value.length) return;
  assetScoresLoading.value = true;
  assetScoresError.value = null;
  // 首载并发 33 个评分请求可能撞上后端 DuckDB 连接建立的锁竞争（瞬态），
  // 与任务中心既有先例一致：静默重试一次（真实第二次调用），仍失败才如实报错。
  const fetchOnce = async (symbol: string): Promise<SentimentScore | "INSUFFICIENT"> => {
    try {
      return (await fetchAssetScore(symbol)).score ?? "INSUFFICIENT";
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 600));
      return (await fetchAssetScore(symbol)).score ?? "INSUFFICIENT";
    }
  };
  const next: Record<string, SentimentScore | "INSUFFICIENT"> = {};
  const failReasons = new Map<string, string>();
  await Promise.all(assets.value.map(async (asset) => {
    try {
      next[asset.symbol] = await fetchOnce(asset.symbol);
    } catch (err) {
      const raw = err as { message?: string };
      failReasons.set(asset.symbol, raw?.message ?? String(err));
      next[asset.symbol] = "INSUFFICIENT";
    }
  }));
  assetScores.value = next;
  if (failReasons.size) {
    const sample = [...failReasons.values()][0]!;
    assetScoresError.value = `${failReasons.size} 只资产评分读取失败（已自动重试一次），已按"证据不足"显示。原因示例：${sample}`;
  }
  assetScoresLoading.value = false;
}

const scoredAssets = computed(() =>
  assets.value.map((asset) => ({
    asset,
    score: assetScores.value[asset.symbol],
  })),
);

/** 八大类别概览：前端按后端资产评分聚合（明确标注，非后端独立类别评分）。 */
const categoryOverview = computed(() => {
  const groups = new Map<string, { total: number; scored: number; direct: number; classOnly: number; insufficient: number; scoreSum: number }>();
  for (const { asset, score } of scoredAssets.value) {
    const key = asset.category;
    const bucket = groups.get(key) ?? { total: 0, scored: 0, direct: 0, classOnly: 0, insufficient: 0, scoreSum: 0 };
    bucket.total += 1;
    if (score && score !== "INSUFFICIENT") {
      bucket.scored += 1;
      bucket.scoreSum += score.sentiment_score;
      bucket.direct += score.direct_evidence_count;
      bucket.classOnly += score.class_evidence_count;
    } else {
      bucket.insufficient += 1;
    }
    groups.set(key, bucket);
  }
  return [...groups.entries()]
    .map(([category, bucket]) => ({
      category,
      ...bucket,
      avgScore: bucket.scored ? bucket.scoreSum / bucket.scored : null,
    }))
    .sort((a, b) => (b.avgScore ?? -999) - (a.avgScore ?? -999));
});

/* ---------------- 新闻与事件 ---------------- */

const events = ref<NewsEvent[]>([]);
const eventsLoading = ref(false);
const eventsError = ref<string | null>(null);
const eventFilterCategory = ref("");
const eventFilterStatus = ref("");
const eventFilterSymbol = ref("");

async function loadEvents() {
  eventsLoading.value = true;
  eventsError.value = null;
  try {
    const query: NewsEventQuery = { limit: 60 };
    if (eventFilterCategory.value) query.category = eventFilterCategory.value;
    if (eventFilterStatus.value) query.analysis_status = eventFilterStatus.value;
    if (eventFilterSymbol.value.trim()) query.symbol = eventFilterSymbol.value.trim().toUpperCase();
    const data = await fetchNewsEvents(query);
    events.value = data.events ?? [];
  } catch (err) {
    eventsError.value = errorMessage(err);
  } finally {
    eventsLoading.value = false;
  }
}

const expandedAsset = ref<string | null>(null);
const assetNews = ref<Record<string, AssetNewsItem[]>>({});
const assetNewsLoading = ref<string | null>(null);

async function toggleAssetNews(symbol: string) {
  if (expandedAsset.value === symbol) {
    expandedAsset.value = null;
    return;
  }
  expandedAsset.value = symbol;
  if (assetNews.value[symbol]) return;
  assetNewsLoading.value = symbol;
  try {
    const data = await fetchAssetNews(symbol, 20);
    assetNews.value = { ...assetNews.value, [symbol]: data.items ?? [] };
  } catch {
    assetNews.value = { ...assetNews.value, [symbol]: [] };
  } finally {
    assetNewsLoading.value = null;
  }
}

/* ---------------- 评分审计（证据下钻） ---------------- */

const evidenceFor = ref<{ title: string; detail: ScoreEvidenceDetail | null } | null>(null);
const evidenceLoading = ref(false);
const evidenceError = ref<string | null>(null);

async function loadEvidence(score: SentimentScore, title: string) {
  evidenceFor.value = { title, detail: null };
  evidenceLoading.value = true;
  evidenceError.value = null;
  try {
    evidenceFor.value = { title, detail: await fetchScoreEvidence(score.score_id) };
  } catch (err) {
    evidenceError.value = errorMessage(err);
  } finally {
    evidenceLoading.value = false;
  }
}

/* ---------------- 数据源状态 ---------------- */

const sources = ref<{ active: Record<string, unknown>[]; rejected: Record<string, unknown>[] } | null>(null);
const sourcesLoading = ref(false);

async function loadSources() {
  sourcesLoading.value = true;
  try {
    const data = await fetchSources();
    const raw = (data.sources ?? []) as Record<string, unknown>[];
    sources.value = {
      active: raw.filter((s) => s.enabled !== false && !s.rejected_reason),
      rejected: raw.filter((s) => s.enabled === false || s.rejected_reason),
    };
  } catch {
    sources.value = { active: [], rejected: [] };
  } finally {
    sourcesLoading.value = false;
  }
}

/* ---------------- 受控写任务（Jobs API + 内联真实轮询） ---------------- */

const activeJob = ref<JobPublic | null>(null);
const jobMessage = ref<string | null>(null);
const jobError = ref<string | null>(null);
const jobSubmitting = ref<string | null>(null);
let jobPollSeq = 0;
const confirmDialog = ref<null | { kind: "refresh" | "analyze" | "rebuild"; text: string }>(null);
const analyzeLimit = ref(8);
const rebuildLookback = ref(168);

async function runJob(kind: "refresh" | "analyze" | "rebuild") {
  jobError.value = null;
  jobSubmitting.value = kind;
  const seq = ++jobPollSeq;
  try {
    let submitted: SentimentJobSubmitData;
    if (kind === "refresh") submitted = await submitNewsRefresh();
    else if (kind === "analyze") submitted = await submitSentimentAnalysis(analyzeLimit.value);
    else submitted = await submitSentimentRebuild(rebuildLookback.value);
    activeJob.value = submitted;
    jobMessage.value = `${submitted.message} · job_id ${submitted.job_id}`;
    // 内联轮询真实进度（3s），完成后刷新情绪数据
    while (seq === jobPollSeq) {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      if (seq !== jobPollSeq) return;
      const job = await fetchJob(submitted.job_id);
      if (!job) { jobError.value = "任务查询失败：job 不存在"; return; }
      activeJob.value = job;
      if (["SUCCEEDED", "FAILED", "CANCELLED"].includes(job.status)) {
        await Promise.all([loadMarket(), loadAssetScores(), loadEvents()]);
        return;
      }
    }
  } catch (err) {
    jobError.value = errorMessage(err);
  } finally {
    if (seq === jobPollSeq) jobSubmitting.value = null;
  }
}

function requestJob(kind: "refresh" | "analyze" | "rebuild") {
  jobError.value = null;
  const texts: Record<typeof kind, string> = {
    refresh: "将提交 refresh_wealth_news 采集任务（真实执行、后端串行队列）。确认执行？",
    analyze: `将提交 analyze_wealth_sentiment 任务：对最近 ${analyzeLimit.value} 条未分析事件做真实付费 LLM 分析（deepseek-flash 优先，qwen 兜底）。确认执行？`,
    rebuild: `将提交 rebuild_wealth_sentiment_factor 任务：按最近 ${rebuildLookback.value} 小时窗口确定性重建情绪分数（无 LLM）。确认执行？`,
  };
  confirmDialog.value = { kind, text: texts[kind] };
}

async function confirmJob() {
  const kind = confirmDialog.value?.kind;
  confirmDialog.value = null;
  if (kind) await runJob(kind);
}

/* ---------------- 展示辅助 ---------------- */

function scoreClass(score: number | null | undefined): string {
  if (score === null || score === undefined) return "";
  return score > 0 ? "up" : score < 0 ? "down" : "";
}

function basisLabel(basis: string | undefined): string {
  switch (basis) {
    case "DIRECT_EVIDENCE": return "直接证据";
    case "CLASS_LEVEL_EVIDENCE": return "间接推导";
    case "INSUFFICIENT_DATA": return "证据不足";
    default: return basis ?? "—";
  }
}

function basisCls(basis: string | undefined): string {
  switch (basis) {
    case "DIRECT_EVIDENCE": return "badge ok";
    case "CLASS_LEVEL_EVIDENCE": return "badge warn";
    default: return "badge";
  }
}

function linkLevelLabel(level: string): string {
  switch (level) {
    case "EXACT": return "直接点名";
    case "SAME_INDEX": return "同指数";
    case "CLASS_ONLY": return "仅类别";
    case "MODEL_INFERRED": return "模型推断";
    default: return level;
  }
}

function fmtTime(iso: string | null | undefined): string {
  return iso ? iso.replace("T", " ").slice(0, 16) : "—";
}

const JOB_STATUS_CLS: Record<string, string> = {
  QUEUED: "badge", RUNNING: "badge action", SUCCEEDED: "badge ok", FAILED: "badge veto", CANCELLED: "badge warn",
};
const JOB_STATUS_LABELS: Record<string, string> = {
  QUEUED: "排队中", RUNNING: "运行中", SUCCEEDED: "成功", FAILED: "失败", CANCELLED: "已取消",
};

onMounted(() => {
  void loadAssets().then(() => Promise.all([loadMarket(), loadAssetScores()]));
  void loadEvents();
  void loadSources();
});
</script>

<template>
  <div class="sentiment-view">
    <p class="page-sub">
      wealth-sentiment/v1.1 · CURRENT_RESEARCH_ONLY（仅研究界面可读，不接入正式策略）。
      只有新闻点名资产代码/全名构成<b>直接证据</b>；仅类别推导的情绪置信度封顶 0.6 并标注"间接推导"；
      无观测即"证据不足"。三个写操作走受控 Jobs API，无假成功。
    </p>

    <div class="layout">
      <!-- 左列：市场总览 + 操作 -->
      <div class="col-side">
        <section class="panel">
          <h3>
            市场总体情绪
            <button class="mini" type="button" @click="loadMarket()">{{ marketLoading ? "刷新中…" : "刷新" }}</button>
          </h3>
          <div v-if="marketError" class="error-box">{{ marketError }}</div>
          <div v-if="marketInsufficient" class="empty">证据不足：{{ marketMessage ?? "窗口内无有效观测" }}</div>
          <template v-else-if="marketScore">
            <div class="hero">
              <span class="hero-score" :class="scoreClass(marketScore.sentiment_score)">
                {{ marketScore.sentiment_score > 0 ? "+" : "" }}{{ marketScore.sentiment_score.toFixed(1) }}
              </span>
              <div class="hero-meta">
                <span :class="basisCls(marketScore.evidence_basis)">{{ basisLabel(marketScore.evidence_basis) }}</span>
                <span class="dim">置信度 {{ (marketScore.confidence * 100).toFixed(1) }}%</span>
              </div>
            </div>
            <div class="evidence-grid">
              <div><span>证据总数</span><b>{{ marketScore.evidence_count }}</b></div>
              <div><span>直接证据</span><b class="up">{{ marketScore.direct_evidence_count }}</b></div>
              <div><span>类别推导</span><b class="gold-text">{{ marketScore.class_evidence_count }}</b></div>
              <div><span>观测/来源</span><b>{{ marketScore.observation_count }} / {{ marketScore.source_count }}</b></div>
            </div>
            <p class="dim-line">版本 {{ marketScore.sentiment_version }} · 快照 {{ fmtTime(marketScore.asof) }} · 最新证据 {{ fmtTime(marketScore.last_evidence_at) }}</p>
            <div v-if="marketScore.component_breakdown?.by_direction" class="dir-chips">
              <span v-for="(count, direction) in marketScore.component_breakdown.by_direction" :key="direction" class="chip static">
                {{ direction }} {{ count }}
              </span>
            </div>
            <button class="mini" type="button" @click="loadEvidence(marketScore, '市场总体情绪评分审计')">查看评分审计（证据下钻）</button>
          </template>
          <div v-else-if="marketLoading" class="loading">读取中…</div>
        </section>

        <section class="panel">
          <h3>受控更新操作（Jobs API）</h3>
          <div class="op-row">
            <button class="mini" type="button" :disabled="jobSubmitting !== null" @click="requestJob('refresh')">更新资讯</button>
            <button class="mini" type="button" :disabled="jobSubmitting !== null" @click="requestJob('analyze')">重新分析</button>
            <button class="mini" type="button" :disabled="jobSubmitting !== null" @click="requestJob('rebuild')">重新计算情绪分数</button>
          </div>
          <div class="mini-form">
            <label>分析条数（1-20）<input v-model.number="analyzeLimit" type="number" min="1" max="20" /></label>
            <label>重建窗口（小时，1-2160）<input v-model.number="rebuildLookback" type="number" min="1" max="2160" /></label>
          </div>
          <p v-if="jobMessage" class="dim-line job-line" role="status">{{ jobMessage }}</p>
          <div v-if="activeJob" class="job-strip">
            <span :class="JOB_STATUS_CLS[activeJob.status] ?? 'badge'">{{ JOB_STATUS_LABELS[activeJob.status] ?? activeJob.status }}</span>
            <span class="progress-cell">
              <span class="progress-bar"><span :style="{ width: `${activeJob.progress}%` }"></span></span>
              {{ activeJob.progress }}%
            </span>
            <code class="mono">{{ activeJob.job_id }}</code>
            <span v-if="activeJob.status === 'SUCCEEDED' && activeJob.result" class="dim">结果：{{ JSON.stringify(activeJob.result).slice(0, 140) }}</span>
            <span v-if="activeJob.error" class="error-text">{{ activeJob.error }}</span>
          </div>
          <p v-if="jobError" class="error-box" role="alert">{{ jobError }}</p>
          <p class="hint">任务在后端串行队列执行；进度为真实轮询（3s）。全量任务管理见「数据中心 → 任务中心」。</p>
        </section>

        <section class="panel">
          <h3>数据源状态</h3>
          <div v-if="sourcesLoading" class="loading">读取中…</div>
          <template v-else-if="sources">
            <div class="src-chips">
              <span v-for="(source, index) in sources.active" :key="`a${index}`" class="badge ok" :title="String(source.description ?? '')">
                {{ String(source.source_name ?? "source") }} · {{ source.runs ?? 0 }} 次
              </span>
            </div>
            <details v-if="sources.rejected.length" class="audit">
              <summary>已弃用来源（{{ sources.rejected.length }}）及原因</summary>
              <pre class="payload">{{ JSON.stringify(sources.rejected, null, 2) }}</pre>
            </details>
          </template>
        </section>
      </div>

      <!-- 右列：类别概览 + 资产表 + 事件 -->
      <div class="col-main">
        <section class="panel">
          <h3>
            资产类别情绪概览（八大类别）
            <span class="h3-extra">前端按后端资产评分聚合（均值，仅供分化概览；后端无独立类别评分）</span>
          </h3>
          <div class="cat-grid">
            <div v-for="cat in categoryOverview" :key="cat.category" class="cat-card">
              <div class="cat-head"><strong>{{ cat.category }}</strong><span class="dim">{{ cat.scored }}/{{ cat.total }} 有分</span></div>
              <div class="cat-score" :class="scoreClass(cat.avgScore)">
                {{ cat.avgScore === null ? "证据不足" : `${cat.avgScore > 0 ? "+" : ""}${cat.avgScore.toFixed(1)}` }}
              </div>
              <div class="cat-sub">直接 {{ cat.direct }} · 间接 {{ cat.classOnly }}<span v-if="cat.insufficient"> · 不足 {{ cat.insufficient }}</span></div>
            </div>
          </div>
        </section>

        <section class="panel">
          <h3>
            研究资产情绪（33 只，逐只后端评分）
            <span class="h3-extra">{{ assetScoresLoading ? "读取中…" : "点击行展开该资产资讯与原文链接" }}</span>
          </h3>
          <div v-if="assetScoresError" class="status-errors" role="alert">{{ assetScoresError }} <button class="mini" type="button" @click="loadAssetScores">重试</button></div>
          <div class="table-scroll">
            <table class="data">
              <thead>
                <tr>
                  <th>资产</th><th>类别</th><th class="num">情绪分</th><th class="num">置信度</th><th>证据口径</th>
                  <th class="num">直接/间接</th><th class="num">观测</th><th>最新证据</th><th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="{ asset, score } in scoredAssets" :key="asset.symbol" class="asset-row" @click="toggleAssetNews(asset.symbol)">
                  <td><strong>{{ asset.name }}</strong><span class="sym-name">{{ asset.symbol }}</span></td>
                  <td>{{ asset.category }}</td>
                  <td class="num" :class="score && score !== 'INSUFFICIENT' ? scoreClass(score.sentiment_score) : 'dim'">
                    <template v-if="score && score !== 'INSUFFICIENT'">{{ score.sentiment_score > 0 ? "+" : "" }}{{ score.sentiment_score.toFixed(1) }}</template>
                    <template v-else>证据不足</template>
                  </td>
                  <td class="num">{{ score && score !== "INSUFFICIENT" ? `${(score.confidence * 100).toFixed(0)}%` : "—" }}</td>
                  <td>
                    <span :class="basisCls(score && score !== 'INSUFFICIENT' ? score.evidence_basis : 'INSUFFICIENT_DATA')">
                      {{ basisLabel(score && score !== "INSUFFICIENT" ? score.evidence_basis : "INSUFFICIENT_DATA") }}
                    </span>
                  </td>
                  <td class="num">{{ score && score !== "INSUFFICIENT" ? `${score.direct_evidence_count} / ${score.class_evidence_count}` : "—" }}</td>
                  <td class="num">{{ score && score !== "INSUFFICIENT" ? score.observation_count : "—" }}</td>
                  <td class="num">{{ score && score !== "INSUFFICIENT" ? fmtTime(score.last_evidence_at) : "—" }}</td>
                  <td>{{ expandedAsset === asset.symbol ? "收起" : "资讯" }}</td>
                </tr>
                <template v-for="{ asset } in scoredAssets" :key="`news-${asset.symbol}`">
                  <tr v-if="expandedAsset === asset.symbol" class="news-row">
                    <td colspan="9">
                      <span v-if="assetNewsLoading === asset.symbol" class="dim">读取资讯中…</span>
                      <template v-else-if="assetNews[asset.symbol]?.length">
                        <div v-for="item in assetNews[asset.symbol]" :key="String(item.news_id ?? item.title)" class="news-item">
                          <span class="news-time">{{ fmtTime(item.published_at as string) }}</span>
                          <span class="news-title">{{ item.title }}</span>
                          <span class="dim">{{ item.source ?? "" }}</span>
                          <a v-if="item.url" :href="String(item.url)" target="_blank" rel="noopener noreferrer">原文 ↗</a>
                          <span v-for="(link, sym) in item.asset_links ?? {}" :key="sym" class="badge" :class="link.level === 'EXACT' ? 'ok' : 'warn'">
                            {{ sym }} · {{ linkLevelLabel(link.level) }}
                          </span>
                        </div>
                      </template>
                      <span v-else class="dim">该资产窗口内无关联资讯（证据不足）。</span>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </section>

        <section class="panel">
          <h3>
            新闻与事件列表
            <span class="h3-extra">同事实重复报道已归并（MERGED 指向规范事件）</span>
          </h3>
          <div class="toolbar">
            <input v-model="eventFilterSymbol" type="text" placeholder="按资产代码过滤（如 518880.SH）" @change="loadEvents" />
            <select v-model="eventFilterCategory" aria-label="按类别过滤" @change="loadEvents">
              <option value="">全部类别</option>
              <option value="MACRO_DATA">宏观数据</option>
              <option value="POLICY">政策</option>
              <option value="COMPANY_ACTION">公司行为</option>
              <option value="GENERAL">综合</option>
            </select>
            <select v-model="eventFilterStatus" aria-label="按分析状态过滤" @change="loadEvents">
              <option value="">全部分析状态</option>
              <option value="ANALYZED">已分析</option>
              <option value="INSUFFICIENT_DATA">证据不足</option>
              <option value="MERGED">已归并</option>
            </select>
            <button class="mini" type="button" @click="loadEvents">{{ eventsLoading ? "刷新中…" : "刷新" }}</button>
          </div>
          <div v-if="eventsError" class="error-box">{{ eventsError }}</div>
          <div class="table-scroll">
            <table class="data">
              <thead>
                <tr><th>发布时间</th><th>事件标题</th><th>方向</th><th>状态</th><th>关联资产（分级）</th><th class="num">新闻/来源</th></tr>
              </thead>
              <tbody>
                <tr v-for="event in events" :key="event.event_id">
                  <td class="num">{{ fmtTime(event.published_at) }}</td>
                  <td class="event-title" :title="event.title">{{ event.title }}</td>
                  <td>
                    <span v-if="event.sentiment_direction" class="badge" :class="event.sentiment_direction === 'POSITIVE' ? 'ok' : event.sentiment_direction === 'NEGATIVE' ? 'veto' : ''">
                      {{ event.sentiment_direction }}
                    </span>
                    <span v-else class="dim">—</span>
                  </td>
                  <td>
                    <span class="badge" :class="event.analysis_status === 'ANALYZED' ? 'action' : event.analysis_status === 'MERGED' ? 'warn' : ''">
                      {{ event.analysis_status }}
                    </span>
                    <small v-if="event.merged_into" class="run-path">→ {{ event.merged_into }}</small>
                  </td>
                  <td>
                    <template v-if="event.asset_links && Object.keys(event.asset_links).length">
                      <span v-for="(link, sym) in event.asset_links" :key="sym" class="badge" :class="link.level === 'EXACT' ? 'ok' : 'warn'">
                        {{ sym }} · {{ linkLevelLabel(link.level) }}
                      </span>
                    </template>
                    <span v-else class="dim">无资产关联</span>
                  </td>
                  <td class="num">{{ event.news_count }} / {{ event.source_count }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>

    <!-- 评分审计抽屉 -->
    <div v-if="evidenceFor" class="modal-mask" @click.self="evidenceFor = null">
      <div class="modal" role="dialog" aria-modal="true" :aria-label="evidenceFor.title">
        <div class="modal-head">
          <h3>{{ evidenceFor.title }}<span class="h3-extra">评分审计 · 每条观测可追溯</span></h3>
          <button class="close-btn" type="button" aria-label="关闭" @click="evidenceFor = null">✕</button>
        </div>
        <div v-if="evidenceLoading" class="loading">读取证据中…</div>
        <div v-if="evidenceError" class="error-box">{{ evidenceError }}</div>
        <template v-if="evidenceFor.detail">
          <div class="plan-grid">
            <div><span>评分快照</span><b><code class="mono">{{ (evidenceFor.detail.score as SentimentScore).score_id }}</code></b></div>
            <div><span>口径</span><b>{{ basisLabel((evidenceFor.detail.score as SentimentScore).evidence_basis) }} · 置信 {{ ((evidenceFor.detail.score as SentimentScore).confidence * 100).toFixed(1) }}%</b></div>
            <div><span>窗口</span><b>{{ (evidenceFor.detail.score as SentimentScore).component_breakdown?.lookback_hours ?? "—" }} 小时 · 事件 {{ (evidenceFor.detail.score as SentimentScore).component_breakdown?.events_in_window ?? "—" }} 条</b></div>
          </div>
          <div v-if="(evidenceFor.detail.score as SentimentScore).component_breakdown?.top_contributors?.length" class="plan-list">
            <div>
              <span>主要贡献事件</span>
              <span v-for="contributor in (evidenceFor.detail.score as SentimentScore).component_breakdown?.top_contributors" :key="contributor.event_id" class="chip static">
                {{ contributor.title.slice(0, 26) }}… {{ contributor.direction }}（贡献 {{ contributor.signed.toFixed(2) }}）
              </span>
            </div>
          </div>
          <h4 class="audit-h4">分析记录（含校验标记）</h4>
          <div v-for="analysis in evidenceFor.detail.analyses" :key="analysis.analysis_id" class="analysis-card">
            <div class="analysis-head">
              <strong>{{ analysis.sentiment_direction ?? "—" }}</strong>
              <code class="mono">{{ analysis.analysis_id }}</code>
            </div>
            <p v-if="analysis.title" class="dim-line">{{ analysis.title }}</p>
            <div v-if="analysis.affected_symbols?.length" class="dir-chips">
              <span v-for="symbol in analysis.affected_symbols" :key="symbol" class="chip static">{{ symbol }}</span>
            </div>
            <div v-if="analysis.validation_flags?.length" class="dir-chips">
              <span v-for="flag in analysis.validation_flags" :key="flag" class="badge warn">{{ flag }}</span>
            </div>
          </div>
          <details class="audit">
            <summary>完整审计 JSON</summary>
            <pre class="payload">{{ JSON.stringify(evidenceFor.detail, null, 2) }}</pre>
          </details>
        </template>
      </div>
    </div>

    <!-- 任务确认对话框 -->
    <div v-if="confirmDialog" class="modal-mask" @click.self="confirmDialog = null">
      <div class="modal confirm-modal" role="alertdialog" aria-modal="true">
        <div class="modal-head"><h3>确认提交任务</h3><button class="close-btn" type="button" aria-label="取消" @click="confirmDialog = null">✕</button></div>
        <p class="confirm-text">{{ confirmDialog.text }}</p>
        <div class="confirm-actions">
          <button class="mini" type="button" @click="confirmDialog = null">取消</button>
          <button class="mini danger" type="button" @click="confirmJob">确认提交</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 380px 1fr; gap: 16px; align-items: start; }
.col-side, .col-main { display: flex; flex-direction: column; min-width: 0; }
.hero { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.hero-score { font-size: 44px; font-weight: 800; font-variant-numeric: tabular-nums; letter-spacing: 1px; }
.hero-meta { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.evidence-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 8px; }
.evidence-grid > div { display: flex; flex-direction: column; gap: 2px; border: 1px solid var(--border); border-radius: var(--radius-ctrl); padding: 7px 10px; background: var(--bg-panel-2); }
.evidence-grid span { font-size: 10.5px; color: var(--text-dim); }
.evidence-grid b { font-size: 15px; font-variant-numeric: tabular-nums; }
.dim-line { margin: 6px 0; font-size: 11.5px; color: var(--text-dim); line-height: 1.6; }
.dir-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.op-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.op-row .mini { padding: 8px 14px; }
.mini-form { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.mini-form label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: var(--text-dim); }
.mini-form input { width: 90px; font-size: 12px; padding: 4px 8px; }
.job-line { color: var(--accent-strong); }
.job-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 8px 0; padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--radius-ctrl); background: var(--bg-panel-2); font-size: 12px; }
.progress-cell { display: inline-flex; align-items: center; gap: 7px; font-variant-numeric: tabular-nums; }
.progress-bar { display: inline-block; width: 70px; height: 6px; border-radius: 4px; background: var(--bg-hover); overflow: hidden; }
.progress-bar > span { display: block; height: 100%; border-radius: 4px; background: var(--accent-grad); transition: width 0.5s ease; }
.src-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.cat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.cat-card { border: 1px solid var(--border); border-radius: var(--radius-ctrl); padding: 10px 12px; background: var(--bg-panel-2); display: flex; flex-direction: column; gap: 4px; }
.cat-head { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; }
.cat-head strong { font-size: 12.5px; }
.cat-score { font-size: 21px; font-weight: 700; font-variant-numeric: tabular-nums; }
.cat-sub { font-size: 11px; color: var(--text-dim); }
.asset-row { cursor: pointer; }
.news-row td { background: var(--bg-hover); white-space: normal; }
.news-item { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 5px 0; border-bottom: 1px solid var(--border-soft); font-size: 12.5px; }
.news-item:last-child { border-bottom: 0; }
.news-time { color: var(--text-faint); font-size: 11px; font-variant-numeric: tabular-nums; }
.news-title { min-width: 0; flex: 1; }
.event-title { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-errors { margin-bottom: 10px; color: var(--veto); font-size: 12px; display: flex; align-items: center; gap: 10px; }
.audit-h4 { margin: 10px 0 6px; font-size: 12.5px; color: var(--text-dim); }
.analysis-card { border: 1px solid var(--border); border-radius: var(--radius-ctrl); padding: 8px 12px; margin-bottom: 8px; background: var(--bg-panel-2); }
.analysis-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.confirm-modal { width: min(560px, 94vw); }
.confirm-text { color: var(--text); font-size: 13px; line-height: 1.7; }
.confirm-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 12px; }
.mono { font-family: Consolas, monospace; font-size: 11.5px; color: var(--accent-strong); }
.plan-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px 16px; margin-bottom: 10px; }
.plan-grid > div { display: flex; flex-direction: column; gap: 3px; }
.plan-grid span { font-size: 11.5px; color: var(--text-dim); }
.plan-grid b { font-size: 13px; word-break: break-all; }
.plan-list > div { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 7px; font-size: 12px; }
.plan-list > div > span:first-child { color: var(--text-dim); margin-right: 4px; }
.chip.static { cursor: default; }
.payload { margin: 0; font-size: 11px; max-height: 300px; overflow: auto; background: var(--control-bg); border: 1px solid var(--border-soft); border-radius: var(--radius-ctrl); padding: 8px 10px; white-space: pre-wrap; word-break: break-all; }
.error-text { color: var(--veto); font-size: 11.5px; }
@media (max-width: 1200px) {
  .layout { grid-template-columns: 1fr; }
  .cat-grid { grid-template-columns: repeat(2, 1fr); }
  .evidence-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
