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
  /** 请求路径与方法 */
  method: "POST";
  path: string;
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
    group: "data",
    label: "同步 A 股研究名录",
    description: "POST /v1/universe/sync：按预算增量同步带版本与审计的 A 股 universe（默认 dry_run 预检）。",
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
    group: "data",
    label: "抓取全市场快照",
    description: "POST /v1/market-snapshots/sync：抓取一批全市场快照（默认 SCREENING 模式、dry_run 预检）。",
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
    group: "data",
    label: "增量补充日线行情",
    description: "POST /v1/data-expansion/run（expansion_type=DAILY_BARS）：按预算补充指定资产日线（dry_run 优先）。",
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
    group: "data",
    label: "候选资产 enrichment",
    description: "POST /v1/candidates/enrich：对 ≤20 只候选资产做研究模式富化（含历史长度与模型预算控制）。",
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
    group: "research",
    label: "新闻与公告分析流水线",
    description: "POST /v1/pipelines/news-analysis：对指定资产在给定日期区间运行新闻+公告分析。",
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
    group: "research",
    label: "官方公告核验流水线",
    description: "POST /v1/pipelines/announcement-verification：对指定资产的官方公告做精确核验。",
    method: "POST",
    path: "/v1/pipelines/announcement-verification",
    params: [
      { key: "symbol", label: "资产代码", kind: "text", default: "600519.SH", required: true },
      { key: "recordId", label: "公告记录 ID", kind: "text", default: "", hint: "选填" },
    ],
  },
  {
    id: "wealth-v2-backtest",
    group: "research",
    label: "运行财富策略回测",
    description: "POST /v1/wealth-lab/v2/strategies/{id}/backtest（8765 可写链路）：后台触发一次真实策略回测并留痕 backtest_id；结果亦可在「策略工厂」页查看。",
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
  /* ---- 以下能力后端契约未交付：按规范显示「暂不可用」，不得伪造 ---- */
  {
    id: "fund-directory-update",
    group: "data",
    label: "更新基金名录",
    description: "后端尚未提供基金/ETF 名录更新的控制接口契约（待后端交付后接入）。",
    unavailable: true,
    unavailableReason: "后端契约未交付：缺少基金名录更新端点（wealth 后端 8765 侧）",
    method: "POST",
    path: "",
    params: [],
  },
  {
    id: "adj-factor-update",
    group: "data",
    label: "更新复权因子",
    description: "后端尚未提供复权因子更新的控制接口契约。",
    unavailable: true,
    unavailableReason: "后端契约未交付：缺少复权因子更新端点",
    method: "POST",
    path: "",
    params: [],
  },
  {
    id: "quality-check",
    group: "data",
    label: "运行数据质量检查",
    description: "质量数据当前由 wealth 后端在审计端点内按需计算（GET /v3/quality），无独立触发式检查任务。",
    unavailable: true,
    unavailableReason: "后端无独立质量检查任务端点；质量审计请见「多资产研究池」页",
    method: "POST",
    path: "",
    params: [],
  },
  {
    id: "pit-rebuild",
    group: "data",
    label: "重新评估历史可投资池",
    description: "PIT 判定为查询时实时计算（GET /v3/eligible），无缓存池重建任务。",
    unavailable: true,
    unavailableReason: "后端无 PIT 池重建任务端点；即时判定请见「多资产研究池」页",
    method: "POST",
    path: "",
    params: [],
  },
  {
    id: "multi-strategy-compare",
    group: "research",
    label: "多策略比较",
    description: "请使用「策略对比与优化」页发起，该页已对接真实对比接口；此处暂无批量任务端点。",
    unavailable: true,
    unavailableReason: "批量比较任务端点未交付；请使用策略对比页的实时接口",
    method: "POST",
    path: "",
    params: [],
  },
  {
    id: "ai-research",
    group: "research",
    label: "触发 AI 研究",
    description: "AI 研究会话在「AI 研究与风控」页交互式发起；无独立后台任务端点。",
    unavailable: true,
    unavailableReason: "AI 研究后台任务端点未交付；请使用 AI 研究页",
    method: "POST",
    path: "",
    params: [],
  },
  {
    id: "risk-check",
    group: "research",
    label: "运行风险检查",
    description: "风险检查随回测与决策包链路执行（wealth 后端），无独立任务端点。",
    unavailable: true,
    unavailableReason: "独立风险检查任务端点未交付；风险结果见回测与决策包",
    method: "POST",
    path: "",
    params: [],
  },
  {
    id: "report-generate",
    group: "research",
    label: "生成研究报告",
    description: "股票研究报告由 stock_report 链路生成，财富侧报告随决策包产出；无独立任务端点。",
    unavailable: true,
    unavailableReason: "报告生成任务端点未交付",
    method: "POST",
    path: "",
    params: [],
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
  return null;
}

/* ---------------- 任务记录（真实调用留痕） ---------------- */

export interface TaskRunRecord {
  runKey: string;
  opId: string;
  opLabel: string;
  path: string;
  request: Record<string, unknown> | null;
  status: "SUCCEEDED" | "FAILED";
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  /** 后端返回的业务 ID（run_id / snapshot_id 等），存在才填 */
  backendRefId: string | null;
  /** 后端异步任务 ID（契约交付后接入轮询；当前同步接口无此字段） */
  jobId: string | null;
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
): Promise<TaskRunRecord> {
  const startedAt = new Date();
  const request = buildPayload(op, params);
  const base: TaskRunRecord = {
    runKey: `${op.id}-${startedAt.getTime()}`,
    opId: op.id,
    opLabel: op.label,
    path: op.path,
    request,
    status: "FAILED",
    startedAt: startedAt.toISOString(),
    finishedAt: startedAt.toISOString(),
    durationMs: 0,
    backendRefId: null,
    jobId: null,
    resultSummary: "",
    resultPayload: null,
    error: null,
  };
  if (!request) {
    return {
      ...base,
      finishedAt: new Date().toISOString(),
      error: "无法构造请求体（参数缺失）",
    };
  }
  const url = op.path.replace("{strategyId}", encodeURIComponent(params.strategyId ?? ""));
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
