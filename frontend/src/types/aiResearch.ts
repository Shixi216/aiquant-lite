/**
 * v3.3 三模型受控 AI 研究 — 类型定义。
 * 1:1 镜像后端契约：trading/wealth_lab/routes_research.py + research/orchestrator.py
 * （ResearchPlan）+ research/audit.py（run/calls 记录）。字段存在性以后端为准，
 * 前端不做推断；可选字段表示后端版本演进中的增量。
 */

import type { WealthEnvelope } from "./wealthLab";

export type { WealthEnvelope };

export interface ResearchRoleSpec {
  role: "PLANNER" | "RESEARCHER" | "REVIEWER" | string;
  display_model_name: string;
  provider: string;
  actual_model_id: string | null;
  available: boolean;
  config_source: string;
  note: string;
}

export interface ResearchModeSpec {
  enabled: boolean;
  model_call_budget: number;
  flag?: string;
}

export interface ResearchRolesData {
  data_mode: string;
  roles: ResearchRoleSpec[];
  modes: {
    STABLE_RESEARCH: ResearchModeSpec;
    EXPERIMENTAL_AI_RESEARCH: ResearchModeSpec;
  };
}

/** DeepSeek Flash Planner 的结构化计划（白名单校验后才能进入实验）。 */
export interface ResearchPlan {
  research_goal: string;
  universe_id: string;
  strategy_candidates: string[];
  start_date: string;
  end_date: string;
  benchmark?: string;
  experiment_params?: Record<string, unknown>;
  constraints?: string[];
  hypotheses?: string[];
}

/** 模型调用审计（audit.wealth_research_calls）。 */
export interface ResearchModelCall {
  call_id: string;
  research_run_id: string;
  agent_role: string;
  display_model_name: string;
  actual_model_id: string | null;
  provider: string;
  prompt_version?: string | null;
  started_at: string | null;
  finished_at: string | null;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  reported_cost?: number | string | null;
  status: string;
  error_reason?: string | null;
  associated_backtest_id?: string | null;
  evidence_ids?: unknown;
}

/** GET /runs/{id} 的运行记录（audit.get_run + model_calls）。 */
export interface ResearchRunRecord {
  research_run_id: string;
  mode: "STABLE" | "EXPERIMENTAL" | string;
  status:
    | "INIT"
    | "PLANNING"
    | "VALIDATION"
    | "APPROVAL_PENDING"
    | "BACKTESTING"
    | "HARD_RISK_CHECK"
    | "RESEARCH"
    | "REVIEW"
    | "VERIFICATION"
    | "COMPLETED"
    | "VETOED"
    | "VERIFICATION_FAILED"
    | "FAILED"
    | string;
  stage: string | null;
  goal: string | null;
  plan_json: string | null;
  backtest_id: string | null;
  decision_packet_id: string | null;
  model_calls_used: number;
  model_call_budget: number;
  experiments_used: number;
  error_reason: string | null;
  created_at: string;
  finished_at: string | null;
  /** GET 详情附带的调用审计 */
  model_calls?: ResearchModelCall[];
}

/** 确定性风险核查结果（WealthRiskEngine.check）。 */
export interface RiskCheckResult {
  verdict: "PASS" | "VETO" | string;
  reasons?: string[];
  checks?: { name: string; passed: boolean; detail?: string }[];
  [key: string]: unknown;
}

/** 数值核验结果（确定性，AI 不替代）。 */
export interface NumericVerification {
  passed: boolean;
  unsupported: string[];
  checked?: number;
  [key: string]: unknown;
}

/** 实验组（真实 v3 回测的摘要）。 */
export interface ResearchExperiment {
  strategy_id: string;
  backtest_id: string;
  metrics: Record<string, number>;
}

/** 研究完成结果（POST runs/* 的 data）。 */
export interface ResearchRunResult {
  research_run_id: string;
  status: "COMPLETED" | "VETOED" | "VERIFICATION_FAILED" | "APPROVAL_PENDING" | string;
  mode?: "STABLE" | "EXPERIMENTAL" | string;
  goal?: string | null;
  backtest_id?: string;
  plan?: ResearchPlan;
  experiments?: ResearchExperiment[];
  risk_check?: RiskCheckResult;
  research?: unknown;
  review?: unknown;
  numeric_verification?: NumericVerification;
  decision_packet_id?: string | null;
  model_calls?: ResearchModelCall[];
  note?: string;
  reused?: boolean;
}

/** 决策包（/v1/wealth-lab/v2/decision-packets/{id}）。 */
export interface WealthDecisionPacket {
  packet_id: string;
  portfolio_id?: string;
  strategy_id?: string;
  strategy_version?: string;
  created_at?: string;
  weights?: Record<string, number>;
  backtest_id?: string | null;
  metrics?: Record<string, number>;
  risk_check?: RiskCheckResult;
  ai_research?: Record<string, unknown> | null;
  ai_risk_review?: Record<string, unknown> | null;
  data_sources?: string[];
  backtest_window?: Record<string, unknown>;
  [key: string]: unknown;
}
