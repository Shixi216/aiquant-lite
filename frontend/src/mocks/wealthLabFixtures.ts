/**
 * 财富实验室契约 Mock 固定样例（离线演示专用）。
 *
 * 来源（唯一权威）：docs/contracts/mocks/*.json —— ZCode 维护的 v1 冻结契约样例，
 * 本文件为逐字拷贝（2026-10-24），仅作 dev server 不可达时的前端降级演示。
 * `satisfies` 约束保证 Mock 结构与 types/wealthLab.ts 编译期一致（Mock↔类型一致性检查）。
 * 契约升版时：先更新 docs/contracts，再同步本文件，禁止擅自改动字段。
 */

import type {
  AssetsData,
  BacktestData,
  MetaData,
  RiskData,
  StressTestData,
  WealthEnvelope,
} from "../types/wealthLab";

export const metaMock = {
  request_id: "req_0123456789abcdef0123456",
  status: "SUCCESS",
  data: {
    data_mode: "REAL",
    contract_version: "wealth-lab/v1",
    engine_version: "wealth-lab-backtest/1.0",
    pool: {
      database: "database/wealth_lab.duckdb",
      row_count: 5466,
      first_date: "2020-01-02",
      last_date: "2026-10-23",
      sources: ["xiaodefa-tushare/fund_daily", "xiaodefa-tushare/fund_adj"],
    },
  },
  error_code: null,
  sanitized_error: null,
  warnings: [],
  risk_flags: [],
  data_cutoff: "2026-10-23T15:00:00+08:00",
  generated_at: "2026-10-24T15:00:00+08:00",
  api_version: "router-0.10.0",
  research_only: true,
} satisfies WealthEnvelope<MetaData>;

export const assetsMock = {
  request_id: "req_0123456789abcdef0123456",
  status: "SUCCESS",
  data: {
    data_mode: "REAL",
    assets: [
      {
        symbol: "510300.SH",
        name: "沪深300ETF",
        category: "EQUITY_ETF",
        default_weight: 0.4,
        listing_date: "2012-05-28",
        data_first_date: "2020-01-02",
        data_last_date: "2026-10-23",
        data_rows: 1648,
        data_status: "REAL",
      },
      {
        symbol: "511010.SH",
        name: "国债ETF",
        category: "BOND_ETF",
        default_weight: 0.4,
        listing_date: "2013-03-25",
        data_first_date: "2020-01-02",
        data_last_date: "2026-10-23",
        data_rows: 1648,
        data_status: "REAL",
      },
      {
        symbol: "518880.SH",
        name: "黄金ETF",
        category: "GOLD_ETF",
        default_weight: 0.1,
        listing_date: "2013-07-29",
        data_first_date: "2020-01-02",
        data_last_date: "2026-10-23",
        data_rows: 1648,
        data_status: "REAL",
      },
      {
        symbol: "511380.SH",
        name: "可转债ETF",
        category: "CONVERTIBLE_ETF",
        default_weight: 0.1,
        listing_date: "2020-03-12",
        data_first_date: "2020-04-07",
        data_last_date: "2026-10-23",
        data_rows: 1587,
        data_status: "REAL",
      },
    ],
  },
  error_code: null,
  sanitized_error: null,
  warnings: [],
  risk_flags: [],
  data_cutoff: "2026-10-23T15:00:00+08:00",
  generated_at: "2026-10-24T15:00:00+08:00",
  api_version: "router-0.10.0",
  research_only: true,
} satisfies WealthEnvelope<AssetsData>;

/** backtest-run-real.json：REAL 模式样例（离线演示时页面必须显著标注“契约 Mock”）。 */
export const backtestRealMock = {
  request_id: "req_0123456789abcdef0123456",
  status: "SUCCESS",
  data: {
    data_mode: "REAL",
    backtest_id: "wlbt_0123456789ab",
    params: {
      weights: { "510300.SH": 0.4, "511010.SH": 0.4, "518880.SH": 0.1, "511380.SH": 0.1 },
      start_date: "2020-04-08",
      end_date: "2026-10-23",
      rebalance: "monthly",
      cost_bps: 5,
      initial_capital: 1000000,
      benchmarks: ["EQUAL_WEIGHT", "510300.SH"],
      allow_demo: false,
    },
    series: {
      dates: [
        "2020-04-08",
        "2020-12-31",
        "2021-12-31",
        "2022-12-30",
        "2023-12-29",
        "2024-06-28",
        "2024-12-31",
        "2025-06-30",
        "2025-12-31",
        "2026-10-23",
      ],
      portfolio_nav: [1.0, 1.0742, 1.1323, 1.1675, 1.2218, 1.2902, 1.3657, 1.4238, 1.5126, 1.5843],
      portfolio_drawdown: [0.0, -0.0321, -0.0458, -0.0683, -0.0512, -0.0387, -0.0264, -0.0332, -0.0198, -0.0145],
      benchmark_nav: [1.0, 1.0284, 1.0564, 1.0345, 1.0701, 1.1448, 1.1916, 1.2216, 1.2809, 1.3306],
      demo: false,
    },
    rebalances: [
      {
        date: "2020-05-06",
        trades: [
          { symbol: "510300.SH", weight_before: 0.4123, weight_after: 0.4, turnover: 0.0123 },
          { symbol: "511010.SH", weight_before: 0.3889, weight_after: 0.4, turnover: 0.0111 },
        ],
        cost: 11.7,
      },
    ],
    metrics: {
      total_return: 0.5843,
      annual_return: 0.0731,
      annual_volatility: 0.0712,
      sharpe_ratio: 0.746,
      max_drawdown: -0.0812,
      calmar_ratio: 0.9,
      monthly_win_rate: 0.6325,
      trading_days: 1587,
    },
    benchmarks: [
      {
        benchmark: "EQUAL_WEIGHT",
        metrics: {
          total_return: 0.3306,
          annual_return: 0.0453,
          annual_volatility: 0.0938,
          sharpe_ratio: 0.27,
          max_drawdown: -0.1672,
          calmar_ratio: 0.271,
          monthly_win_rate: 0.5714,
          trading_days: 1587,
        },
      },
      {
        benchmark: "510300.SH",
        metrics: {
          total_return: 0.0845,
          annual_return: 0.0121,
          annual_volatility: 0.2042,
          sharpe_ratio: -0.0402,
          max_drawdown: -0.4533,
          calmar_ratio: 0.027,
          monthly_win_rate: 0.5064,
          trading_days: 1587,
        },
      },
    ],
    data_quality: {
      adjusted: true,
      sources: ["xiaodefa-tushare/fund_daily", "xiaodefa-tushare/fund_adj"],
      missing_days: 0,
      caveats: ["净值基于 fund_adj 后复权因子；共同交易日历自 2020-04-07 起"],
    },
  },
  error_code: null,
  sanitized_error: null,
  warnings: [],
  risk_flags: [],
  data_cutoff: "2026-10-23T15:00:00+08:00",
  generated_at: "2026-10-24T15:00:00+08:00",
  api_version: "router-0.10.0",
  research_only: true,
} satisfies WealthEnvelope<BacktestData>;

/** backtest-run-demo.json：DEMO 合成数据样例（响应含 demo:true 与 DEMO_MODE_SYNTHETIC_DATA 警示）。 */
export const backtestDemoMock = {
  request_id: "req_0123456789abcdef0123456",
  status: "SUCCESS",
  data: {
    data_mode: "DEMO",
    backtest_id: "wlbt_0123456789ab",
    params: {
      weights: { "510300.SH": 0.4, "511010.SH": 0.4, "518880.SH": 0.1, "511380.SH": 0.1 },
      start_date: "2020-04-08",
      end_date: "2026-10-23",
      rebalance: "monthly",
      cost_bps: 5,
      initial_capital: 1000000,
      benchmarks: ["EQUAL_WEIGHT", "510300.SH"],
      allow_demo: false,
    },
    series: {
      dates: [
        "2020-04-08",
        "2020-12-31",
        "2021-12-31",
        "2022-12-30",
        "2023-12-29",
        "2024-06-28",
        "2024-12-31",
        "2025-06-30",
        "2025-12-31",
        "2026-10-23",
      ],
      portfolio_nav: [1.0, 1.014, 1.0202, 1.0181, 1.0147, 1.0182, 1.0305, 1.0455, 1.0543, 1.0541],
      portfolio_drawdown: [0.0, -0.0321, -0.0458, -0.0683, -0.0512, -0.0387, -0.0264, -0.0332, -0.0198, -0.0145],
      benchmark_nav: [1.0, 1.0033, 1.0067, 1.01, 1.0133, 1.0167, 1.02, 1.0233, 1.0267, 1.03],
      demo: true,
    },
    rebalances: [
      {
        date: "2020-05-06",
        trades: [
          { symbol: "510300.SH", weight_before: 0.4123, weight_after: 0.4, turnover: 0.0123 },
          { symbol: "511010.SH", weight_before: 0.3889, weight_after: 0.4, turnover: 0.0111 },
        ],
        cost: 11.7,
      },
    ],
    metrics: {
      total_return: 0.0556,
      annual_return: 0.0078,
      annual_volatility: 0.0201,
      sharpe_ratio: -0.0609,
      max_drawdown: -0.0081,
      calmar_ratio: 0.963,
      monthly_win_rate: 0.5,
      trading_days: 10,
    },
    benchmarks: [
      {
        benchmark: "EQUAL_WEIGHT",
        metrics: {
          total_return: 0.3306,
          annual_return: 0.0453,
          annual_volatility: 0.0938,
          sharpe_ratio: 0.27,
          max_drawdown: -0.1672,
          calmar_ratio: 0.271,
          monthly_win_rate: 0.5714,
          trading_days: 1587,
        },
      },
      {
        benchmark: "510300.SH",
        metrics: {
          total_return: 0.0845,
          annual_return: 0.0121,
          annual_volatility: 0.2042,
          sharpe_ratio: -0.0402,
          max_drawdown: -0.4533,
          calmar_ratio: 0.027,
          monthly_win_rate: 0.5064,
          trading_days: 1587,
        },
      },
    ],
    data_quality: {
      adjusted: false,
      sources: ["synthetic"],
      missing_days: 0,
      caveats: ["DEMO 合成数据：仅供前端联调，禁止用于任何真实结论"],
    },
  },
  error_code: null,
  sanitized_error: null,
  warnings: ["DEMO_MODE_SYNTHETIC_DATA"],
  risk_flags: [],
  data_cutoff: "2026-10-23T15:00:00+08:00",
  generated_at: "2026-10-24T15:00:00+08:00",
  api_version: "router-0.10.0",
  research_only: true,
} satisfies WealthEnvelope<BacktestData>;

/** backtest-run-unavailable.json：FAILED / DATA_UNAVAILABLE 错误信封样例。 */
export const backtestUnavailableMock = {
  request_id: "req_0123456789abcdef0123456",
  status: "FAILED",
  data: null,
  error_code: "DATA_UNAVAILABLE",
  sanitized_error: "数据不可用：请求区间早于资产上市日",
  warnings: [],
  risk_flags: [],
  data_cutoff: "2026-10-23T15:00:00+08:00",
  generated_at: "2026-10-24T15:00:00+08:00",
  api_version: "router-0.10.0",
  research_only: true,
} satisfies WealthEnvelope<BacktestData>;

export const riskMock = {
  request_id: "req_0123456789abcdef0123456",
  status: "SUCCESS",
  data: {
    data_mode: "REAL",
    backtest_id: "wlbt_0123456789ab",
    metrics: {
      total_return: 0.5843,
      annual_return: 0.0731,
      annual_volatility: 0.0712,
      sharpe_ratio: 0.746,
      max_drawdown: -0.0812,
      calmar_ratio: 0.9,
      monthly_win_rate: 0.6325,
      trading_days: 1587,
    },
    rolling: {
      dates: [
        "2020-12-31",
        "2021-12-31",
        "2022-12-30",
        "2023-12-29",
        "2024-06-28",
        "2024-12-31",
        "2025-06-30",
        "2025-12-31",
        "2026-10-23",
      ],
      volatility_63d: [0.0812, 0.0745, 0.0698, 0.0721, 0.0688, 0.0654, 0.0632, 0.0601, 0.0587],
      sharpe_252d: [0.51, 0.62, 0.68, 0.71, 0.69, 0.73, 0.76, 0.75, 0.78],
      drawdown: [-0.0321, -0.0458, -0.0683, -0.0512, -0.0387, -0.0264, -0.0332, -0.0198, -0.0145],
    },
    worst_days: [
      { date: "2024-01-22", daily_return: -0.0212 },
      { date: "2024-02-05", daily_return: -0.0187 },
    ],
    value_at_risk_95_1d: -0.0068,
  },
  error_code: null,
  sanitized_error: null,
  warnings: [],
  risk_flags: [],
  data_cutoff: "2026-10-23T15:00:00+08:00",
  generated_at: "2026-10-24T15:00:00+08:00",
  api_version: "router-0.10.0",
  research_only: true,
} satisfies WealthEnvelope<RiskData>;

/** stress-historical.json：2020-03 疫情冲击（历史窗口情景）。 */
export const stressHistoricalMock = {
  request_id: "req_0123456789abcdef0123456",
  status: "SUCCESS",
  data: {
    data_mode: "REAL",
    scenario_label: "2020-03 疫情冲击",
    portfolio_shock_return: -0.0341,
    portfolio_nav_path: [
      { date: "2020-03-05", nav: 1.0 },
      { date: "2020-03-19", nav: 0.9541 },
      { date: "2020-03-31", nav: 0.9659 },
    ],
    asset_contributions: [
      { symbol: "510300.SH", weight: 0.4, shock_return: -0.1213, contribution: -0.0485 },
      { symbol: "511010.SH", weight: 0.4, shock_return: 0.0091, contribution: 0.0036 },
      { symbol: "518880.SH", weight: 0.1, shock_return: 0.0384, contribution: 0.0038 },
      { symbol: "511380.SH", weight: 0.1, shock_return: -0.0021, contribution: -0.0002 },
    ],
  },
  error_code: null,
  sanitized_error: null,
  warnings: [],
  risk_flags: [],
  data_cutoff: "2026-10-23T15:00:00+08:00",
  generated_at: "2026-10-24T15:00:00+08:00",
  api_version: "router-0.10.0",
  research_only: true,
} satisfies WealthEnvelope<StressTestData>;

/** stress-synthetic.json：股债金三杀（合成冲击情景）。 */
export const stressSyntheticMock = {
  request_id: "req_0123456789abcdef0123456",
  status: "SUCCESS",
  data: {
    data_mode: "DEMO",
    scenario_label: "股债金三杀（合成）",
    portfolio_shock_return: -0.058,
    portfolio_nav_path: [
      { date: "2026-10-23", nav: 1.0 },
      { date: "2026-10-24", nav: 0.942 },
    ],
    asset_contributions: [
      { symbol: "510300.SH", weight: 0.4, shock_return: -0.1, contribution: -0.04 },
      { symbol: "511010.SH", weight: 0.4, shock_return: -0.02, contribution: -0.008 },
      { symbol: "518880.SH", weight: 0.1, shock_return: -0.08, contribution: -0.008 },
      { symbol: "511380.SH", weight: 0.1, shock_return: -0.02, contribution: -0.002 },
    ],
  },
  error_code: null,
  sanitized_error: null,
  warnings: [],
  risk_flags: [],
  data_cutoff: "2026-10-23T15:00:00+08:00",
  generated_at: "2026-10-24T15:00:00+08:00",
  api_version: "router-0.10.0",
  research_only: true,
} satisfies WealthEnvelope<StressTestData>;
