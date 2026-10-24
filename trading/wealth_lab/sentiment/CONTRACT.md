# Wealth Sentiment API 契约 v1（wealth-sentiment/v1）

> 供 GLM 前端接入的独立新增契约。**不修改**任何既有冻结契约（wealth-lab/v1、v2、v3）与股票业务接口。
> 全部响应走既有 envelope：`{request_id, status, data, warnings, risk_flags, generated_at, api_version: "wealth-sentiment/v1", research_only: true}`。
> 模块实现：`trading/wealth_lab/sentiment/`；数据独立落 `database/wealth_sentiment.duckdb`。

## 基本约定

- 所有情绪输出为**研究指标**（`research_only=true`）：`sentiment_score ∈ [-100, +100]`，不等于预期收益方向，不构成统计预测概率。
- 资产代码均来自 Wealth Lab 研究池（33 只：A 股宽基/行业/风格/红利 ETF、国债/政金债/可转债 ETF、黄金/豆粕商品 ETF、公募 REITs、恒生/DAX/纳指/标普/油气 QDII）。
- 任何写操作都通过 v3.2 任务控制中心（`/v1/wealth-lab/v3/jobs`）串行执行，返回 `job_id`，前端轮询 jobs 接口获取真实进度——**后端真实执行，绝无假成功**。

---

## 查询接口（GET，全部幂等只读）

### 1. `GET /v1/wealth-lab/sentiment/news/events`
查询新闻事件（去重归并后）。

| 参数 | 类型 | 说明 |
|---|---|---|
| `category` | str 可选 | MONETARY_POLICY / MACRO_DATA / FUND_ANNOUNCE / GOLD_COMMODITY / BOND_MARKET / QDII_OVERSEAS / GENERAL |
| `symbol` | str 可选 | 池内代码，如 `518880.SH`（命中事件级或条目级关联） |
| `analysis_status` | str 可选 | PENDING / ANALYZED / INSUFFICIENT_DATA |
| `since` | str 可选 | ISO 时间 |
| `limit` | int | 默认 50，≤200 |

响应 `data`: `{data_mode:"REAL", events:[{event_id, title, published_at, published_at_precision, raw_category, asset_scope:[symbol], news_count, source_count, analysis_status, first_seen_at, sentiment_direction|null, analysis_id|null}], count}`

### 2. `GET /v1/wealth-lab/sentiment/sources`
数据源状态与统计。响应 `data`: `{data_mode, sources:[{source_name, provider, enabled, description, doc, runs, ok_runs, last_run_at, avg_field_completeness, total_new, latest_published_at}], rejected:{源名:弃用原因}}`

### 3. `GET /v1/wealth-lab/sentiment/assets/{symbol}/news`
指定资产关联资讯（条目级）。`data`: `{symbol, items:[{news_id, title, summary, source_name, source_url, published_at, quality_status, event_id, raw_category, first_seen_at}], count}`

### 4. `GET /v1/wealth-lab/sentiment/market`
市场总体情绪（最新 MARKET 快照）。`data.score`：

```json
{
  "score_id": "wscore_...",
  "level": "MARKET", "symbol": null,
  "asof": "2026-10-25T01:42:58+08:00",
  "sentiment_score": 22.63, "confidence": 0.6346, "coverage": 1.0,
  "observation_count": 36, "source_count": 36,
  "sentiment_version": "wealth-sentiment/1.0",
  "evidence_ids": ["wana_...", "..."],
  "component_breakdown": {
    "by_direction": {"POSITIVE": 13, "NEGATIVE": 7, "NEUTRAL": 2, "MIXED": 12},
    "top_contributors": [{"event_id","title","direction","signed","decay","confidence"}],
    "weights": { /* 版本化聚合权重全量快照 */ },
    "lookback_hours": 168, "events_in_window": 36
  }
}
```
无快照时 `data.score=null` 且附 `message` 提示先执行 rebuild 任务。

### 5. `GET /v1/wealth-lab/sentiment/market/history?days=30`
市场情绪历史（`history:[score...]`，不含 breakdown）。

### 6. `GET /v1/wealth-lab/sentiment/assets/{symbol}/score`
资产情绪评分（最新 ASSET 快照，结构同 market.score，`symbol` 为该资产）。

### 7. `GET /v1/wealth-lab/sentiment/assets/{symbol}/history?days=30`
资产情绪历史。

### 8. `GET /v1/wealth-lab/sentiment/scores/{score_id}/evidence`
评分证据链：`data.score` = 快照 + `analyses:[{analysis_id, event_id, title, published_at, event_type, affected_asset_classes, affected_symbols, sentiment_direction, estimated_impact, confidence, evidence_spans, uncertainty, explanation, validation_flags, model_id, provider, prompt_version, raw_category, news_count, source_count}]`。前端可直接渲染"为什么是这个分"。

---

## 任务提交接口（POST，走 v3.2 任务控制中心白名单）

白名单新增 3 个操作（`/v1/wealth-lab/v3/jobs` 同一套状态机/幂等/取消/审计）：

### 9. `POST /v1/wealth-lab/sentiment/refresh`
body: `{sources?: [source_name], fund_symbols?: [symbol]}`（均可省略=全部启用源）
→ 提交 `refresh_wealth_news` 任务。响应含 `job_id/status/whitelist`。

### 10. `POST /v1/wealth-lab/sentiment/analyze`
body: `{limit: 1..20, confirm: true}`（**必须 confirm=true**，真实付费模型调用；deepseek-flash 优先，qwen 兜底）
→ 提交 `analyze_wealth_sentiment`。缺 confirm 返回 422。

### 11. `POST /v1/wealth-lab/sentiment/rebuild-factor`
body: `{lookback_hours: 1..2160}`（默认 168）
→ 提交 `rebuild_wealth_sentiment_factor`（纯确定性聚合，无 LLM 调用）。

任务进度/日志/取消复用既有接口：
`GET /v1/wealth-lab/v3/jobs`、`GET .../jobs/{job_id}`、`GET .../jobs/{job_id}/logs`、`POST .../jobs/{job_id}/cancel`。

---

## 前端建议用法

1. "刷新资讯" 按钮 → `POST /refresh` → 轮询 jobs → 完成后调 `GET /sources` 展示各源真实条数/完整率。
2. "AI 分析" 按钮（需二次确认）→ `POST /analyze {confirm:true}` → 轮询 → `GET /news/events` 刷新。
3. 情绪面板 → `GET /market` + `GET /assets/{symbol}/score`；点击分数 → `GET /scores/{id}/evidence` 展示证据链。
4. 多资产对比 → 对 33 资产并发 `GET /assets/{symbol}/score`（或用 history 拉趋势）。

## 隔离承诺（不变式）

- 不写任何既有业务表；不修改 S0-S8 策略、WealthRiskEngine 硬 VETO、股票新闻情绪与评分流程。
- LLM 无工具权限，不控制采集范围与写入；新闻正文按不可信输入处理。
- 情绪因子仅可被研究界面读取；进入实验性策略需另行授权与历史验证。
