# Wealth Sentiment API 契约 v1.1（wealth-sentiment/v1）

> 供 GLM 前端接入的独立新增契约。**不修改**任何既有冻结契约（wealth-lab/v1、v2、v3）与股票业务接口。
> 响应 envelope：`{request_id, status, data, warnings, risk_flags, generated_at, api_version: "wealth-sentiment/v1", research_only: true}`。
> 模块：`trading/wealth_lab/sentiment/`；数据独立落 `database/wealth_sentiment.duckdb`。
> **状态：CURRENT_RESEARCH_ONLY** —— 未验证历史可获得性前不得用于历史回测，不得接入 S0-S8 正式策略。

## v1.1 变更摘要（相对 v1.0）

1. **关联分级**：事件/条目新增 `asset_links`（`{symbol: {level, rule}}`），`level ∈ EXACT | SAME_INDEX | CLASS_ONLY | MODEL_INFERRED`。只有 EXACT（新闻点名 6 位代码或完整登记名称）构成**直接证据**；池外产品新闻（如 159655）对池内同类产品最多 SAME_INDEX。
2. **事件归并**：同一事实操作（同主体+同操作+同金额+±1 日）归并为一个事件，重复报道不再重复计权；重复事件 `analysis_status='MERGED'`、`merged_into=canonical`。
3. **评分可信度字段**：`evidence_basis / evidence_count / direct_evidence_count / class_evidence_count / coverage_ratio / last_evidence_at`。仅类别推导的个券情绪 `evidence_basis=CLASS_LEVEL_EVIDENCE` 且 confidence ≤ 0.6。
4. 评分版本 `wealth-sentiment/1.1`（v1.0 快照保留于历史可对比）。

---

## 1. 评分对象结构（`/market` 与 `/assets/{symbol}/score` 的 `data.score`）

真实响应样例（2026-10-25 实测，`GET /v1/wealth-lab/sentiment/market`，节选）：

```json
{
  "request_id": "req_…", "status": "SUCCESS", "api_version": "wealth-sentiment/v1",
  "research_only": true,
  "data": {
    "data_mode": "REAL",
    "score": {
      "score_id": "wscore_…",
      "level": "MARKET", "symbol": null,
      "asof": "2026-10-25T10:55:31+08:00",
      "sentiment_score": 17.4,
      "confidence": 0.6426,
      "coverage": 1.0, "coverage_ratio": 1.0,
      "observation_count": 53, "source_count": 53,
      "sentiment_version": "wealth-sentiment/1.1",
      "evidence_basis": "DIRECT_EVIDENCE",
      "evidence_count": 53,
      "direct_evidence_count": 53,
      "class_evidence_count": 0,
      "last_evidence_at": "2026-10-25T01:31:39+08:00",
      "evidence_ids": ["wana_…", "…"],
      "component_breakdown": {
        "by_direction": {"POSITIVE": 26, "NEGATIVE": 20, "NEUTRAL": 4, "MIXED": 3},
        "by_link_level": {"EXACT": 0, "SAME_INDEX": 0, "MODEL_INFERRED": 0, "CLASS_ONLY": 0},
        "top_contributors": [
          {"event_id": "wevt_…", "title": "央行开展7000亿元1年期MLF操作",
           "direction": "POSITIVE", "signed": 0.66, "link_level": null,
           "decay": 0.87, "confidence": 0.72}
        ],
        "weights": {"weights_id": "v1.1", "link_weight": {"EXACT": 1.0, "SAME_INDEX": 0.35, "MODEL_INFERRED": 0.35, "CLASS_ONLY": 0.25}, "…": "…"},
        "lookback_hours": 168, "events_in_window": 53
      }
    }
  }
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `sentiment_score` | float | -100~+100，研究指标，非收益预测 |
| `confidence` | float | 0~1；`CLASS_LEVEL_EVIDENCE` 时封顶 0.6；覆盖不足进一步压低 |
| `coverage` / `coverage_ratio` | float | 同值冗余两个字段名，观测数/目标观测数（资产 6、市场 10），≤1 |
| `evidence_basis` | enum | `DIRECT_EVIDENCE`（≥1 条 EXACT 观测）/ `CLASS_LEVEL_EVIDENCE`（仅主题或类别推导）/ `INSUFFICIENT_DATA`（无快照时在 data 层返回） |
| `evidence_count` | int | 有效观测总数 = direct + class |
| `direct_evidence_count` | int | EXACT 级观测数（新闻点名该产品） |
| `class_evidence_count` | int | 间接观测数（主题/类别/模型推断） |
| `last_evidence_at` | ISO 时间 | 最新一条观测事件的发布时间 |
| `evidence_ids` | [str] | 贡献最大的分析记录 id（≤10），供 `/scores/{id}/evidence` 下钻 |
| `sentiment_version` | str | 聚合权重版本，永久保留 |
| `component_breakdown.weights` | object | 该快照使用的全部权重（可审计、可复现） |

资产级真实样例（`GET /v1/wealth-lab/sentiment/assets/518880.SH/score`，2026-10-25 实测）：
`sentiment_score=-33.09, confidence=0.6, evidence_basis=CLASS_LEVEL_EVIDENCE, direct_evidence_count=0, class_evidence_count=12, last_evidence_at=2026-10-25T01:31:39+08:00`（金价新闻为主题级推导，前端应以较低可信度呈现）。

**REITs/QDII/低覆盖资产渲染建议**：`evidence_basis=CLASS_LEVEL_EVIDENCE` 且 `observation_count<3` 的分数建议灰显并标注"间接推导"；`direct_evidence_count=0` 不得展示为强信号。

## 2. 事件查询 `GET /news/events`

参数同 v1.0（category/symbol/analysis_status/since/limit）。事件对象新增：

- `asset_links`：`{"513500.SH": {"level": "SAME_INDEX", "rule": "theme:标普"}}` —— 分级关联明细；
- `merged_into`：被归并的重复事件指向规范事件 id（`analysis_status='MERGED'`）；
- `analysis_status` 新增取值 `MERGED`。

## 3. 数据源状态 `GET /sources`

同 v1.0（6 个登记源 + 5 个实测弃用源及原因）。真实样例见 `scripts/wealth_sentiment/real_response_samples.json`。

## 4. 资产资讯 `GET /assets/{symbol}/news`

同 v1.0。条目按 `asset_links` 命中该 symbol（任意层级）返回。

## 5. 历史 `GET /market/history?days=` 与 `GET /assets/{symbol}/history?days=`

同 v1.0；v1.0 与 v1.1 快照共存于历史，按 `sentiment_version` 区分。

## 6. 证据下钻 `GET /scores/{score_id}/evidence`

同 v1.0 结构；`analyses[]` 新增 `validation_flags` 中可能出现的 v1.1 标记：
`cleared_symbols_on_insufficient` / `capped_insufficient_confidence` / `deterministic_event_type_refined` / `llm_symbols_without_deterministic_link` / `llm_symbols_not_exact` / `inherited_from_merge`。

## 7. 任务提交（复用 v3.2 Jobs API，白名单 3 操作）

- `POST /v1/wealth-lab/sentiment/refresh` body `{sources?, fund_symbols?}` → `refresh_wealth_news`
- `POST /v1/wealth-lab/sentiment/analyze` body `{limit:1..20, confirm:true}`（必须 confirm）→ `analyze_wealth_sentiment`（deepseek-flash 优先，qwen 兜底；拒答事件自动清空资产断言并封顶置信度）
- `POST /v1/wealth-lab/sentiment/rebuild-factor` body `{lookback_hours:1..2160}` → `rebuild_wealth_sentiment_factor`（重建前自动执行存量重分级+事件归并，纯确定性无 LLM）

进度/取消复用：`GET/POST /v1/wealth-lab/v3/jobs*`。任务真实执行、失败如实返回，无假成功。

## 8. 不变式（对前端的承诺）

- 三个写任务之外无任何写路径；LLM 无工具权限、不控制采集与写入。
- 情绪因子仅研究界面可读；接入实验性策略需另行授权与历史验证（CURRENT_RESEARCH_ONLY）。
- 不修改 S0-S8、WealthRiskEngine、冻结契约与股票业务。
