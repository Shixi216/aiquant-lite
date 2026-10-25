/**
 * 数据与研究任务中心 API 服务层（唯一 HTTP 出口）。
 *
 * 原则（与 WEALTH_LAB_HANDOFF.md 一致）：
 * - 全部走统一 API Client（/api → Router 8767；财富侧复用 api/wealthLab*.ts）；
 * - 请求体严格按后端既有 Pydantic 契约（data_hub/schemas/full_market.py、router/schemas/*）；
 * - 后端同步接口：请求返回即终态，前端如实记录耗时与结果，不模拟进度；
 *   后端一旦交付异步 job 契约（job_id + 轮询/事件流），在 run.jobId 上接入即可；
 * - 后端未提供的能力：按钮置灰显示「暂不可用」，绝不伪造成功。
 */

import axios from "axios";

import { api, errorMessage } from "./client";

/** 财富后端（8765）出口：任务中心仅通过 /wealth-lab-api 代理调用，不直连端口。 */
const wealthApi = axios.create({ baseURL: "/wealth-lab-api", timeout: 120_000 });

/* ---------------- 状态面板（GET，只读） ---------------- */

export interface RouterHealth {
  status: string;
  [key: string]: unknown;
}

export interface ProviderHealthItem {
  provider?: string;
  status?: string;
  healthy?: boolean;
  [key: string]: unknown;
}

export interface MarketSnapshotLatest {
  snapshot_id: string;
  mode: string;
  data_cutoff?: string;
  fetched_at?: string;
  stale?: boolean;
  items?: unknown[];
  [key: string]: unknown;
}

export interface DataCoverageLatest {
  coverage_id?: string;
  generated_at?: string;
  [key: string]: unknown;
}

export interface UniverseHead {
  universe_version: string | null;
  total: number;
}

export interface TaskCenterStatus {
  routerHealth: RouterHealth | null;
  providers: ProviderHealthItem[] | null;
  snapshot: MarketSnapshotLatest | null;
  coverage: DataCoverageLatest | null;
  universe: UniverseHead | null;
  errors: string[];
  fetchedAt: string;
}

function pick<T>(value: unknown): T | null {
  return (value ?? null) as T | null;
}

export async function fetchTaskCenterStatus(): Promise<TaskCenterStatus> {
  const errors: string[] = [];
  const safe = async <T>(label: string, call: () => Promise<T>): Promise<T | null> => {
    try {
      return await call();
    } catch (err) {
      errors.push(`${label}：${errorMessage(err)}`);
      return null;
    }
  };
  const [routerHealth, providersResp, snapshot, coverage, universeResp] = await Promise.all([
    safe("Router 健康检查", () => api.get<RouterHealth>("/health").then((r) => r.data)),
    safe("数据源状态", () => api.get<{ providers?: ProviderHealthItem[] }>("/v1/providers/health").then((r) => r.data)),
    safe("全市场快照", () =>
      api
        .get<MarketSnapshotLatest>("/v1/market-snapshots/latest", { params: { maximum_age_seconds: 86_400, limit: 1 } })
        .then((r) => r.data)),
    safe("数据覆盖报告", () => api.get<DataCoverageLatest>("/v1/data-coverage/latest").then((r) => r.data)),
    safe("A股名录", () =>
      api.get<UniverseListRaw>("/v1/universe", { params: { limit: 1, active_only: false } }).then((r) => r.data)),
  ]);
  return {
    routerHealth: pick<RouterHealth>(routerHealth),
    providers: pick<{ providers?: ProviderHealthItem[] }>(providersResp)?.providers ?? null,
    snapshot,
    coverage,
    universe: universeResp ? { universe_version: universeResp.universe_version, total: universeResp.total } : null,
    errors,
    fetchedAt: new Date().toISOString(),
  };
}

interface UniverseListRaw {
  universe_version: string | null;
  total: number;
  offset: number;
  limit: number;
  items: unknown[];
}

/* ================= 后端任务队列（v3.2 jobs 契约，/wealth-lab-api） =================
 * 契约：trading/wealth_lab/routes_jobs.py（POST /v1/wealth-lab/v3/jobs 等）。
 * 受控提交（白名单 8 操作）→ job_id → 轮询真实状态/进度 → 终态留痕。
 * 付费 AI 操作必须 params.confirm=true；大规模数据任务由后端串行队列保证 DuckDB 单写者。
 */

export type JobStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

export interface JobPublic {
  job_id: string;
  operation: string;
  request_id: string | null;
  status: JobStatus;
  progress: number;
  source: string | null;
  asset_scope: string[];
  records_written: number;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

function unwrapJobEnvelope<T>(payload: { data?: T } | T): T {
  // wealth 信封：{status, data:{...}}；jobs 路由统一 envelope_v3
  const outer = payload as { data?: T };
  if (outer && typeof outer === "object" && "data" in outer && outer.data && typeof outer.data === "object") {
    return outer.data;
  }
  return payload as T;
}

export async function submitJob(
  operation: string,
  params: Record<string, unknown>,
): Promise<{ job: JobPublic; duplicated: boolean; message: string }> {
  const payload = await wealthApi.post("/v1/wealth-lab/v3/jobs", { operation, params }).then((r) => r.data);
  const data = unwrapJobEnvelope<Record<string, unknown>>(payload);
  const { duplicated, message, ...job } = data;
  return { job: job as unknown as JobPublic, duplicated: Boolean(duplicated), message: String(message ?? "") };
}

export async function fetchJobs(status?: string): Promise<JobPublic[]> {
  const payload = await wealthApi
    .get("/v1/wealth-lab/v3/jobs", { params: status ? { status } : {} })
    .then((r) => r.data);
  const data = unwrapJobEnvelope<{ jobs?: JobPublic[] }>(payload);
  return data.jobs ?? [];
}

export async function fetchJob(jobId: string): Promise<JobPublic | null> {
  try {
    const payload = await wealthApi.get(`/v1/wealth-lab/v3/jobs/${encodeURIComponent(jobId)}`).then((r) => r.data);
    return unwrapJobEnvelope<JobPublic>(payload);
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) return null;
    throw err;
  }
}

export async function fetchJobLogs(jobId: string): Promise<{ status: string; logs: string[] }> {
  const payload = await wealthApi.get(`/v1/wealth-lab/v3/jobs/${encodeURIComponent(jobId)}/logs`).then((r) => r.data);
  const data = unwrapJobEnvelope<{ status: string; logs: string[] }>(payload);
  return { status: data.status, logs: data.logs ?? [] };
}

export async function cancelJob(jobId: string, reason?: string): Promise<JobPublic | null> {
  try {
    const payload = await wealthApi
      .post(`/v1/wealth-lab/v3/jobs/${encodeURIComponent(jobId)}/cancel`, { reason: reason ?? null })
      .then((r) => r.data);
    return unwrapJobEnvelope<JobPublic>(payload);
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) return null;
    throw err;
  }
}

export const JOB_OPERATION_LABELS: Record<string, string> = {
  update_fund_basic: "更新基金名录",
  update_market_data: "增量更新 ETF 行情",
  update_adj_factors: "更新复权因子",
  run_quality_check: "运行质量检查",
  rebuild_pit: "重新评估历史可投资池",
  run_backtest: "策略回测（后台）",
  run_ai_research: "AI 研究（付费）",
  generate_report: "生成研究报告",
};

const TERMINAL_STATUSES: JobStatus[] = ["SUCCEEDED", "FAILED", "CANCELLED"];

/* ---------------- 操作注册表（POST，受控写操作） ---------------- */

export type OpGroupId = "data" | "research";

export interface TaskOpParamSpec {
  key: string;
  label: string;
  kind: "text" | "date" | "number" | "select" | "symbols";
  options?: string[];
  default?: string | number;
  required?: boolean;
  hint?: string;
}

export interface TaskOpDef {
  id: string;
  group: OpGroupId;
  label: string;
  description: string;
  /** 确认对话框文案；为空则不二次确认 */
  confirm?: string;
  /** 执行模式：sync = 同步接口（请求返回即终态）；job = 后端任务队列（job_id + 轮询） */
  kind: "sync" | "job";
  /** kind=job 时的后端白名单操作名 */
  operation?: string;
  /** 请求路径与方法（kind=sync 用） */
  method?: "POST";
  path?: string;
  /** 出口：api = Router 门面（/api，当前部署为 GET-only 只读代理）；wealth = 财富后端（/wealth-lab-api，POST 可用） */
  base?: "api" | "wealth";
  /** 参数 schema；UI 据此渲染表单 */
  params: TaskOpParamSpec[];
  /** true = 后端契约未交付，按钮显示「暂不可用」 */
  unavailable?: boolean;
  unavailableReason?: string;
}

const nowIso = () => new Date().toISOString();
const todayStr = () => new Date().toISOString().slice(0, 10);

/** data_cutoff：后端要求带时区的 datetime，默认取当前时刻（ISO 8601）。 */
function cutoffFrom(params: Record<string, string>): string {
  const raw = params.dataCutoff?.trim();
  if (raw) {
    // 日期-only 输入 → 视为当日 16:00（东八区收盘后）
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return `${raw}T16:00:00+08:00`;
    return raw;
  }
  return nowIso();
}

function splitSymbols(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(/[,，\s]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
}

export const TASK_OPS: TaskOpDef[] = [
  {
    id: "universe-sync",
    kind: "sync",
    group: "data",
    label: "同步 A 股研究名录",
    description: "按预算同步最新 A 股研究名录（带版本与审计记录）。默认仅预检、不写入，可在参数中关闭预检。",
    method: "POST",
    path: "/v1/universe/sync",
    params: [
      { key: "dryRun", label: "dry_run 预检", kind: "select", options: ["true", "false"], default: "true", hint: "true=只做预检不写库；正式落库请切 false" },
      { key: "requestBudget", label: "请求预算", kind: "number", default: 3, hint: "0-20" },
      { key: "dataCutoff", label: "数据截止", kind: "text", default: "", hint: "留空=现在；或 YYYY-MM-DD（按当日 16:00 +08:00）" },
    ],
  },
  {
    id: "snapshot-sync",
    kind: "sync",
    group: "data",
    label: "抓取全市场快照",
    description: "抓取一批全市场行情快照，用于市场宽度与个股监控。默认仅预检、不写入。",
    method: "POST",
    path: "/v1/market-snapshots/sync",
    params: [
      { key: "dryRun", label: "dry_run 预检", kind: "select", options: ["true", "false"], default: "true" },
      { key: "requestBudget", label: "请求预算", kind: "number", default: 1, hint: "0-5" },
      { key: "dataCutoff", label: "数据截止", kind: "text", default: "" },
    ],
  },
  {
    id: "data-expansion-daily-bars",
    kind: "sync",
    group: "data",
    label: "增量补充日线行情",
    description: "按预算为指定资产补充日线行情（可指定区间），默认仅预检。适合补齐少数资产的历史缺口。",
    confirm: "将按预算向后端请求补充日线行情。请确认预算与资产列表。",
    method: "POST",
    path: "/v1/data-expansion/run",
    params: [
      { key: "dryRun", label: "dry_run 预检", kind: "select", options: ["true", "false"], default: "true" },
      { key: "requestBudget", label: "请求预算", kind: "number", default: 10 },
      { key: "dataCutoff", label: "数据截止", kind: "text", default: "" },
      { key: "symbols", label: "资产列表", kind: "symbols", default: "600519.SH, 000858.SZ", hint: "逗号分隔，≤5000 个；留空则由后端按池决定" },
      { key: "startDate", label: "开始日期", kind: "date", default: todayStr() },
      { key: "endDate", label: "结束日期", kind: "date", default: todayStr() },
    ],
  },
  {
    id: "candidates-enrich",
    kind: "sync",
    group: "data",
    label: "候选资产 enrichment",
    description: "对不超过 20 只候选资产做研究资料补全（含历史长度与调用预算控制）。",
    method: "POST",
    path: "/v1/candidates/enrich",
    params: [
      { key: "symbols", label: "资产列表", kind: "symbols", default: "600519.SH", hint: "必填，逗号分隔，1-20 只" },
      { key: "dryRun", label: "dry_run 预检", kind: "select", options: ["true", "false"], default: "true" },
      { key: "requestBudget", label: "请求预算", kind: "number", default: 0 },
      { key: "modelCallBudget", label: "模型调用预算", kind: "number", default: 0 },
      { key: "dataCutoff", label: "数据截止", kind: "text", default: "" },
    ],
  },
  {
    id: "news-pipeline",
    kind: "sync",
    group: "research",
    label: "新闻与公告分析流水线",
    description: "对指定资产在给定日期区间运行新闻与公告分析流水线。",
    method: "POST",
    path: "/v1/pipelines/news-analysis",
    params: [
      { key: "symbol", label: "资产代码", kind: "text", default: "600519.SH", required: true },
      { key: "startDate", label: "开始日期", kind: "date", default: todayStr() },
      { key: "endDate", label: "结束日期", kind: "date", default: todayStr() },
      { key: "financeNewsLimit", label: "财经新闻条数", kind: "number", default: 5, hint: "1-20" },
    ],
  },
  {
    id: "announcement-verification",
    kind: "sync",
    group: "research",
    label: "官方公告核验流水线",
    description: "对指定资产的官方公告做精确核验，产出可追溯的核验结论。",
    method: "POST",
    path: "/v1/pipelines/announcement-verification",
    params: [
      { key: "symbol", label: "资产代码", kind: "text", default: "600519.SH", required: true },
      { key: "recordId", label: "公告记录 ID", kind: "text", default: "", hint: "选填" },
    ],
  },
  {
    id: "wealth-v2-backtest",
    kind: "sync",
    group: "research",
    label: "运行财富策略回测",
    description: "立即触发一次真实策略回测，完成后生成回测编号，可在「策略工厂」页查看完整结果。",
    method: "POST",
    path: "/v1/wealth-lab/v2/strategies/{strategyId}/backtest",
    base: "wealth",
    params: [
      { key: "strategyId", label: "策略 ID", kind: "select", options: ["S0_buy_and_hold_core4", "S2_cross_momentum", "S3_dual_momentum", "S4_inverse_volatility"], default: "S2_cross_momentum", required: true },
      { key: "startDate", label: "开始日期", kind: "date", default: "2020-04-08" },
      { key: "endDate", label: "结束日期", kind: "date", default: "2026-10-23" },
      { key: "costBps", label: "单边成本(bps)", kind: "number", default: 5 },
    ],
  },
  /* ---- 以下走后端任务队列（v3.2 jobs 白名单）：提交 → job_id → 真实轮询 ---- */
  {
    id: "job-update-fund-basic",
    group: "data",
    label: "更新基金名录",
    description: "jobs/update_fund_basic：拉取官方 fund_basic（E+O）核对研究池元数据（上市/退市/名称），差异写入 notes。",
    kind: "job",
    operation: "update_fund_basic",
    params: [],
  },
  {
    id: "job-update-market-data",
    group: "data",
    label: "增量更新 ETF 行情",
    description: "jobs/update_market_data：按池内资产从各自 data_last 次日起增量抓取入审计库；可指定资产与区间。",
    confirm: "将向研究池资产行情库增量写入数据（后端串行队列执行）。请确认参数。",
    kind: "job",
    operation: "update_market_data",
    params: [
      { key: "symbols", label: "资产列表", kind: "symbols", default: "", hint: "留空=整个研究池；或逗号分隔（须在池内）" },
      { key: "startDate", label: "开始日期", kind: "text", default: "", hint: "YYYYMMDD；留空=各资产 data_last 次日" },
      { key: "endDate", label: "结束日期", kind: "text", default: "", hint: "YYYYMMDD；留空=今天" },
    ],
  },
  {
    id: "job-update-adj-factors",
    group: "data",
    label: "更新复权因子",
    description: "jobs/update_adj_factors：随行情重拉 fund_adj 并强制校验缺失（缺失即失败，不填充）。",
    confirm: "复权因子校验失败即任务失败（不填充）。确认执行？",
    kind: "job",
    operation: "update_adj_factors",
    params: [],
  },
  {
    id: "job-run-quality-check",
    group: "data",
    label: "运行质量检查",
    description: "jobs/run_quality_check：以池内真实交易日历重算缺失率并更新质量状态（QUALIFIED/DEGRADED/REJECTED）。",
    confirm: "将重算并写回各资产质量状态。确认执行？",
    kind: "job",
    operation: "run_quality_check",
    params: [],
  },
  {
    id: "job-rebuild-pit",
    group: "data",
    label: "重新评估历史可投资池",
    description: "jobs/rebuild_pit：对每个资产池最近 12 个月初时点重评 PIT 资格，产出快照。",
    confirm: "将对全部资产池 × 12 个时点重评 PIT（计算量较大）。确认执行？",
    kind: "job",
    operation: "rebuild_pit",
    params: [],
  },
  {
    id: "job-run-backtest",
    group: "research",
    label: "启动策略回测（后台）",
    description: "jobs/run_backtest：以任务形式运行 v3 资产池回测，完成后 result 附 backtest_id/time_semantics/metrics。",
    kind: "job",
    operation: "run_backtest",
    params: [
      { key: "strategyId", label: "策略 ID", kind: "select", options: ["S0_fixed_weight", "S1_equal_weight", "S2_cross_momentum", "S3_dual_momentum", "S4_inverse_volatility"], default: "S0_fixed_weight", required: true },
      { key: "universeId", label: "资产池 ID", kind: "text", default: "multi-asset-v2", required: true },
      { key: "startDate", label: "开始日期", kind: "date", default: "2021-02-04" },
      { key: "endDate", label: "结束日期", kind: "date", default: "2026-10-23" },
      { key: "costBps", label: "单边成本(bps)", kind: "number", default: 5 },
    ],
  },
  {
    id: "job-run-ai-research",
    group: "research",
    label: "触发 AI 研究（付费）",
    description: "jobs/run_ai_research：对指定回测结果运行 AI 研究+风险复核（真实模型调用，须显式确认）。",
    confirm: "该操作触发真实付费模型调用（confirm=true）。请确认回测 ID 无误。",
    kind: "job",
    operation: "run_ai_research",
    params: [
      { key: "backtestId", label: "回测 ID", kind: "text", default: "", required: true, hint: "如 wlbt_xxxx（可从策略工厂或任务记录获取）" },
    ],
  },
  {
    id: "job-generate-report",
    group: "research",
    label: "生成研究报告",
    description: "jobs/generate_report：从存档回测重放生成月度收益/指标报告。",
    kind: "job",
    operation: "generate_report",
    params: [
      { key: "backtestId", label: "回测 ID", kind: "text", default: "", required: true },
    ],
  },
];

/** 按参数表单值构造符合后端契约的请求体；每个 op 显式映射，不自创格式。 */
export function buildPayload(op: TaskOpDef, params: Record<string, string>): Record<string, unknown> | null {
  switch (op.id) {
    case "universe-sync":
      return {
        analysis_mode: "RESEARCH",
        dry_run: params.dryRun !== "false",
        provider: "AUTO",
        request_budget: Number(params.requestBudget ?? 3),
        data_cutoff: cutoffFrom(params),
      };
    case "snapshot-sync":
      return {
        analysis_mode: "SCREENING",
        dry_run: params.dryRun !== "false",
        provider: "AKSHARE",
        request_budget: Number(params.requestBudget ?? 1),
        data_cutoff: cutoffFrom(params),
      };
    case "data-expansion-daily-bars": {
      const symbols = splitSymbols(params.symbols);
      const payload: Record<string, unknown> = {
        analysis_mode: "RESEARCH",
        dry_run: params.dryRun !== "false",
        provider: "AUTO",
        request_budget: Number(params.requestBudget ?? 10),
        data_cutoff: cutoffFrom(params),
        expansion_type: "DAILY_BARS",
        symbols,
        batch_size: 50,
        resume: false,
      };
      if (params.startDate) payload.start_date = params.startDate;
      if (params.endDate) payload.end_date = params.endDate;
      return payload;
    }
    case "candidates-enrich":
      return {
        analysis_mode: "RESEARCH",
        dry_run: params.dryRun !== "false",
        provider: "AUTO",
        request_budget: Number(params.requestBudget ?? 0),
        model_call_budget: Number(params.modelCallBudget ?? 0),
        data_cutoff: cutoffFrom(params),
        symbols: splitSymbols(params.symbols),
      };
    case "news-pipeline": {
      const payload: Record<string, unknown> = {
        symbol: params.symbol?.trim(),
        start_date: params.startDate,
        end_date: params.endDate,
        finance_news_limit: Number(params.financeNewsLimit ?? 5),
      };
      return payload;
    }
    case "announcement-verification": {
      const payload: Record<string, unknown> = { symbol: params.symbol?.trim() };
      if (params.recordId?.trim()) payload.record_id = params.recordId.trim();
      return payload;
    }
    case "wealth-v2-backtest":
      return {
        start_date: params.startDate,
        end_date: params.endDate,
        cost_bps: Number(params.costBps ?? 5),
      };
    default:
      return null;
  }
}

/** op 专属的必填校验（返回错误文案；null 表示通过）。 */
export function validateOp(op: TaskOpDef, params: Record<string, string>): string | null {
  for (const spec of op.params) {
    if (spec.required && !(params[spec.key] ?? "").trim()) return `请填写「${spec.label}」`;
  }
  if (op.id === "candidates-enrich") {
    const symbols = splitSymbols(params.symbols);
    if (!symbols.length) return "请至少填写 1 只资产代码";
    if (symbols.length > 20) return "candidates/enrich 单次最多 20 只资产";
  }
  if (op.id === "news-pipeline" && params.startDate && params.endDate && params.startDate > params.endDate) {
    return "开始日期不能晚于结束日期";
  }
  if (op.id === "wealth-v2-backtest" && params.startDate && params.endDate && params.startDate >= params.endDate) {
    return "开始日期必须早于结束日期";
  }
  if (op.id === "job-run-backtest" && params.startDate && params.endDate && params.startDate >= params.endDate) {
    return "开始日期必须早于结束日期";
  }
  if (op.kind === "job" && (op.id === "job-run-ai-research" || op.id === "job-generate-report")) {
    if (!(params.backtestId ?? "").trim()) return "请填写回测 ID（如 wlbt_xxxx）";
  }
  if (op.id === "job-update-market-data") {
    for (const key of ["startDate", "endDate"]) {
      const raw = (params[key] ?? "").trim();
      if (raw && !/^\d{8}$/.test(raw)) return "日期格式须为 YYYYMMDD（或留空使用默认口径）";
    }
  }
  return null;
}

/** job 操作参数构造（严格按 job_handlers.py 各 handler 的 params 读取口径）。 */
export function buildJobParams(op: TaskOpDef, params: Record<string, string>): Record<string, unknown> {
  switch (op.operation) {
    case "update_market_data": {
      const payload: Record<string, unknown> = {};
      const symbols = splitSymbols(params.symbols);
      if (symbols.length) payload.symbols = symbols;
      if (params.startDate?.trim()) payload.start_date = params.startDate.trim();
      if (params.endDate?.trim()) payload.end_date = params.endDate.trim();
      return payload;
    }
    case "run_backtest": {
      const payload: Record<string, unknown> = {
        strategy_id: params.strategyId?.trim(),
        universe_id: params.universeId?.trim(),
        cost_bps: Number(params.costBps ?? 5),
      };
      if (params.startDate) payload.start_date = params.startDate;
      if (params.endDate) payload.end_date = params.endDate;
      return payload;
    }
    case "run_ai_research":
      return { backtest_id: params.backtestId?.trim(), confirm: true };
    case "generate_report":
      return { backtest_id: params.backtestId?.trim() };
    default:
      return {};
  }
}

/* ---------------- 任务记录（真实调用留痕） ---------------- */

export interface TaskRunRecord {
  runKey: string;
  opId: string;
  opLabel: string;
  path: string;
  request: Record<string, unknown> | null;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  /** 后端返回的业务 ID（run_id / snapshot_id / backtest_id 等），存在才填 */
  backendRefId: string | null;
  /** 后端异步任务 ID（jobs 契约） */
  jobId: string | null;
  /** 任务进度 0-100（job 模式轮询更新） */
  progress: number | null;
  /** 已写入记录数（job 模式实时更新） */
  recordsWritten: number | null;
  /** 任务结果引用（job 终态 result_ref） */
  resultRef: Record<string, unknown> | null;
  resultSummary: string;
  resultPayload: unknown;
  error: string | null;
}

/** 统一信封取内层 data（财富 API 返回 {status, data:{...}}；直返型接口无信封）。 */
function innerData(payload: unknown): Record<string, unknown> {
  const outer = (payload ?? {}) as Record<string, unknown>;
  if (outer.data && typeof outer.data === "object" && !Array.isArray(outer.data)) {
    return outer.data as Record<string, unknown>;
  }
  return outer;
}

function summarize(opId: string, payload: unknown): string {
  const record = innerData(payload);
  const parts: string[] = [];
  const candidates = ["run_id", "snapshot_id", "backtest_id", "status", "mode", "provider", "requested", "succeeded", "failed", "skipped", "total", "annual_return", "sharpe_ratio"];
  for (const key of candidates) {
    if (record[key] !== undefined && record[key] !== null && record[key] !== "") parts.push(`${key}=${String(record[key])}`);
  }
  if (!parts.length) return "已返回（无摘要字段）";
  return parts.join(" · ");
}

export function backendRefIdOf(payload: unknown): string | null {
  const record = innerData(payload);
  for (const key of ["run_id", "snapshot_id", "job_id", "backtest_id", "coverage_id"]) {
    const value = record[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}

/** 只读门面的 405：给出准确的现状解释，而不是笼统的"请求未完成"。 */
function operationError(err: unknown): string {
  if (axios.isAxiosError(err) && err.response?.status === 405) {
    return "后端当前部署为只读可视化服务（GET-only 代理），该写操作未开放（HTTP 405）。待后端交付任务控制契约并开放写链路后可用——本次未产生任何数据变更。";
  }
  return errorMessage(err);
}

export async function executeOp(
  op: TaskOpDef,
  params: Record<string, string>,
  onTick?: (record: TaskRunRecord) => void,
): Promise<TaskRunRecord> {
  const startedAt = new Date();
  const request = buildPayload(op, params);
  const base: TaskRunRecord = {
    runKey: `${op.id}-${startedAt.getTime()}`,
    opId: op.id,
    opLabel: op.label,
    path: op.path ?? "",
    request,
    status: "FAILED",
    startedAt: startedAt.toISOString(),
    finishedAt: startedAt.toISOString(),
    durationMs: 0,
    backendRefId: null,
    jobId: null,
    progress: null,
    recordsWritten: null,
    resultRef: null,
    resultSummary: "",
    resultPayload: null,
    error: null,
  };
  if (op.kind === "job") {
    return executeJobOp(op, base, buildJobParams(op, params), onTick);
  }
  if (!request) {
    return {
      ...base,
      finishedAt: new Date().toISOString(),
      error: "无法构造请求体（参数缺失）",
    };
  }
  const url = (op.path ?? "").replace("{strategyId}", encodeURIComponent(params.strategyId ?? ""));
  const http = op.base === "wealth" ? wealthApi : api;
  base.path = url;
  try {
    const response = await http.post(url, request, { timeout: 180_000 });
    const payload = response.data;
    const finishedAt = new Date();
    return {
      ...base,
      status: "SUCCEEDED",
      finishedAt: finishedAt.toISOString(),
      durationMs: finishedAt.getTime() - startedAt.getTime(),
      backendRefId: backendRefIdOf(payload),
      resultSummary: summarize(op.id, payload),
      resultPayload: payload,
    };
  } catch (err) {
    const finishedAt = new Date();
    return {
      ...base,
      finishedAt: finishedAt.toISOString(),
      durationMs: finishedAt.getTime() - startedAt.getTime(),
      error: operationError(err),
    };
  }
}

/** job 模式：提交 → 每 2s 轮询真实状态/进度（不模拟）→ 终态落痕；onTick 供页面实时刷新。 */
export async function executeJobOp(
  op: TaskOpDef,
  base: TaskRunRecord,
  jobParams: Record<string, unknown>,
  onTick?: (record: TaskRunRecord) => void,
): Promise<TaskRunRecord> {
  const startedAt = new Date(base.startedAt);
  try {
    const submitted = await submitJob(op.operation!, jobParams);
    const submittedAt = new Date();
    let current: TaskRunRecord = {
      ...base,
      path: `POST /v1/wealth-lab/v3/jobs（operation=${op.operation}）`,
      request: { operation: op.operation, params: jobParams },
      status: submitted.job.status,
      jobId: submitted.job.job_id,
      progress: submitted.job.progress,
      recordsWritten: submitted.job.records_written,
      resultSummary: submitted.message + (submitted.duplicated ? "（幂等命中进行中任务）" : ""),
    };
    onTick?.(current);
    // 轮询：2s 间隔，上限 5 分钟
    const deadline = Date.now() + 300_000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const job = await fetchJob(submitted.job.job_id);
      if (!job) {
        current = { ...current, status: "FAILED", error: "任务查询失败：job 不存在" };
        onTick?.(current);
        break;
      }
      current = {
        ...current,
        status: job.status,
        progress: job.progress,
        recordsWritten: job.records_written,
        backendRefId: current.backendRefId ?? (typeof job.result?.backtest_id === "string" ? job.result.backtest_id : null),
        resultRef: job.result ?? null,
        resultSummary: jobProgressSummary(job),
        finishedAt: job.finished_at ?? current.finishedAt,
      };
      onTick?.(current);
      if (TERMINAL_STATUSES.includes(job.status)) {
        const finishedAt = new Date();
        return {
          ...current,
          status: job.status,
          finishedAt: finishedAt.toISOString(),
          durationMs: finishedAt.getTime() - startedAt.getTime(),
          error: job.error,
          resultSummary: job.status === "SUCCEEDED" ? jobProgressSummary(job) : job.error ?? "任务失败",
        };
      }
    }
    return { ...current, status: "FAILED", error: "轮询超时（5 分钟）：任务仍在后端执行，可在后端任务队列查看后续状态" };
  } catch (err) {
    const finishedAt = new Date();
    return {
      ...base,
      path: `POST /v1/wealth-lab/v3/jobs（operation=${op.operation}）`,
      finishedAt: finishedAt.toISOString(),
      durationMs: finishedAt.getTime() - startedAt.getTime(),
      error: operationError(err),
    };
  }
}

function jobProgressSummary(job: JobPublic): string {
  const parts = [`${job.progress}%`];
  if (job.records_written) parts.push(`写入 ${job.records_written} 行`);
  if (job.source) parts.push(job.source);
  const result = (job.result ?? {}) as Record<string, unknown>;
  for (const key of ["updated", "rows", "symbols", "backtest_id", "research_id", "review_id", "ai_verdict"]) {
    if (result[key] !== undefined) parts.push(`${key}=${String(result[key])}`);
  }
  return parts.join(" · ");
}
