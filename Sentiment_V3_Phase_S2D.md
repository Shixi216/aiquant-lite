# Sentiment V3 Phase S2D

## 1. 范围与冻结边界

S2D 仅建设 Research / Shadow 文本、事件、反应数据层。所有新对象 `formal_weight = 0`；未接入 stock_report、Scanner、DecisionEngine、正式 VETO、A/B Shadow 或 Canonical Shadow；未生成统一分数。

三层定义：

- News Sentiment：观点、预期变化、市场反应叙事、传闻。它描述文本表达的倾向。
- Event Intelligence：客观发生了什么、何时可见、方向、强度、事实状态及证据。
- Event Reaction：事件可用之后，股票相对市场/行业的确定性行情反应。

新闻数量、来源数量和新颖文档数量明确标为 `ATTENTION_PROXY`，不等同于 sentiment。

## 2. 现有 V2 文本链审计

### 2.1 数据入口与持久化

- 新闻：`FinanceNewsService` 经 AKShare 东方财富新闻进入 `data_records(data_type=finance_news)`；历史库另有 `Eastmoney`、`global finance` 来源。
- 公告：`AnnouncementService` 经 AKShare 巨潮资讯进入 `data_records(data_type=announcement)`；历史库另有 `CNInfo`、批量 AKShare/东方财富来源。
- `MarketRecord` 使用 UUID `record_id`，并保存 `event_time`、`fetched_at`、source 与 content hash；没有独立 `available_time`。
- 原内容 hash 都未包含正文。新闻旧 hash 为 source/symbol/type/title/published_at/url；公告旧 hash 为 source/symbol/type/title/date/url。

实库审计时：公告 1,843 条且 content hash 均唯一，其中 1,823 条 `event_time` 恰为 00:00；新闻 1,054 条且 content hash 均唯一。唯一 hash 只能说明存储层精确去重，不能说明同一事件跨媒体不重复。

### 2.2 旧聚类与调用

`EventClusterService` 只对相同 content hash，或 24 小时内“标准化标题相同且 symbol 重叠”聚类。canonical ID 依赖 data_type、标题、symbol 和日期，所以公告与媒体报道不能稳定聚为同一真实事件，改写标题也无法覆盖。

V2 查询新闻和公告后只取 payload，按 event_time 排序，混合进入 40% 的 event sentiment。批处理名义上 batch，实际每个 EventBundle 各调用一次模型，默认最多 8 个并发。公告主路由 LongCat、新闻主路由 Qwen，失败互为备用；确定性关键词 fallback 再输出方向、强度和期限。高风险/低置信度/高强度候选才进入 DeepSeek 风险复核，并非所有文本无条件重复调用。

旧 Qwen schema 仅有事件类型、`-1/0/1` 方向、强度、置信度、影响期限、fact type、影响股票/板块和摘要。缺失 text type、surprise、novelty、source quality、fact status、available time 和可追溯 evidence。

### 2.3 旧缓存与 PIT 风险

- 外层 V2 缓存键仅 symbol + date；内层提取缓存也主要是 symbol + date，未绑定内容、模型、Prompt、schema 和算法版本。
- `build_bundles_from_records` 在发布时间缺失或解析失败时使用当前时间，并使用 Python 进程随机化 `hash(title)` 生成部分 ID，历史不稳定。
- 日期型公告被截为 00:00，可能在当天开盘前错误可见。
- 新闻与公告混算，事实方向被当作市场情绪；固定 source 权重、强度/风险阈值继续存在于 V2。
- stock_report 的 `--no-ai` 走旧关键词情绪，AI 模式走旧模型链；政策因子又单独消费公告，存在语义重复。S2D 未修改这些旧行为。

模型调用审计：`model_calls` 734 条；Qwen news_processor 557（556 成功），DeepSeek risk_controller 135（42 成功），其余为 LongCat、公告 Qwen、DeepSeek news_processor 与 MiMo。S2D 不改正式 DeepSeek 链。

## 3. S2D 对象契约

### 3.1 Qwen schema

`TextExtractionOutput`：

- `text_type`: FACT_EVENT / EXPECTATION_CHANGE / MARKET_REACTION / OPINION / RISK / RUMOR / OTHER
- `event_type`: EARNINGS、EARNINGS_FORECAST、DIVIDEND、BUYBACK、SHAREHOLDER_INCREASE、SHAREHOLDER_REDUCTION、CONTRACT、ORDER、M_AND_A、ASSET_RESTRUCTURING、FINANCING、EQUITY_INCENTIVE、LITIGATION、PENALTY、REGULATORY、INVESTIGATION、ST_STATUS、DELISTING_RISK、MANAGEMENT_CHANGE、MAJOR_SHAREHOLDER_CHANGE、PRODUCT、CAPACITY、POLICY、OTHER
- `direction`: POSITIVE / NEGATIVE / NEUTRAL / MIXED / UNKNOWN
- `intensity`, `confidence`, `horizon`, `novelty`, `surprise`, `surprise_basis`, `event_time`, `available_time`, `source_quality`, `fact_status`, `evidence`

`surprise` 只有明确预期、指引、consensus 或 forecast 基准才保留；schema 要求 surprise 必须有 basis，Qwen 后处理还会检查原文是否存在预期基准。规则 fallback 只识别“超预期/超过预期/不及预期/低于预期”等显式表述，绝不推测 surprise。

`evidence` 最多 8 段、每段最多 240 字，只保留短证据，不保存模型隐藏推理。

### 3.2 模型与 Prompt 版本

- Prompt：`TEXT_INTELLIGENCE_PROMPT_V1`；同时保留 News/Event 专用版本常量。
- schema：`1.4.0`。
- text algorithm：`sentiment-v3-text-s2d-1.0.0`。
- 记录 provider、实际返回 model name（源仅提供名称时照实记录）、Prompt/schema/algorithm version、processed_at、latency 和可用时 token 统计。

## 4. 去重、canonical event 与缓存

### 4.1 两层去重

精确去重 hash 对规范化 `title + body + source + published_time` 做 SHA-256。相同 hash 在一次处理内只保留一条。

语义同事件使用 `CANONICAL_EVENT_RULE_V1`：symbol + event_type + 事件日期 + 文本内数值事实生成稳定 `canonical_event_id`。因此“拟回购5亿元”和“宣布最高5亿元回购计划”可合并；这是保守规则，不是完整语义模型。数值缺失、事件跨日更新或复杂多实体仍可能漏合并。

同一 canonical event 保留全部文档 hash；官方公告/监管来源使 `official_confirmation_exists=true`，只提升事实确认语义，不改变事件方向。新来源或澄清作为 revision 保存，`supersedes_event_id` 指向上一版本，as-of 只看到当时可见 revision。

### 4.2 增量缓存

缓存键：`content_hash + provider + model_name + prompt_version + schema_version + algorithm_version`。命中即读 DuckDB 结构化结果，不调用模型。Prompt、模型、schema 或算法版本变化会重新处理。

真实验证一条文本：首次 cache miss 1、Qwen 调用 1、成功 STRUCTURED_AI，Qwen latency 4,771 ms，总耗时 5,001.679 ms；第二次 cache hit 1、Qwen 调用 0，总耗时 69.414 ms。缓存 lookup 首次 84.997 ms、二次 66.322 ms。无新增文本时新增 LLM 调用为 0。

失败时使用 `RULE_FALLBACK`，增加 `LOW_CONFIDENCE`，并独立统计 `qwen_failures`、`fallback_calls`。规则只覆盖高确定性事件，不计算行情、不生成复杂预期。

## 5. 时间和 PIT

文本时间独立保存：

- `event_time`：事件实际发生时间，未知为 None。
- `published_time`：来源发布时间。
- `available_time`：本系统在真实世界可使用的时间。
- `ingested_time`：进入本地数据层时间。
- `processed_at`：结构化处理完成时间。

只有日期的公告采用保守 18:00 推断；如果系统在更早时已经实际抓到，则以 ingestion time 作为“已观察到”的上界，并标 `AVAILABLE_TIME_INFERRED`。新闻缺发布时间时不伪造 published_time，使用 ingestion time 作为 available_time，并标 `PUBLISHED_TIME_UNKNOWN`。

事件时段：PRE_MARKET / IN_SESSION / POST_MARKET / NON_TRADING_DAY / UNKNOWN。盘后、盘中日线事件和周末事件不能读取同日最终收盘作为事后反应，必须进入下一交易日。交易日选择由调用方复用现有交易日历；S2D 计算器对传入本地 Snapshot 再做日期和 available_time 校验。

## 6. Event Reaction

Reaction 只消费传入的本地 Market、Sector、Stock 和可选 Stock Capital Snapshot，不含任何 AKShare/Tushare/腾讯/LLM 调用。目前只实现 PIT 安全的 EOD 与 NEXT_SESSION 窗口；OPEN、INTRADAY 和 MULTI_DAY 仅保留枚举，调用会明确拒绝。

连续变量全部保留：stock/market/sector return、relative market/sector return、volume change、turnover change、CLV、open gap、high-to-close drawdown、low-to-close recovery，以及同窗口合法时的 vendor flow ratio 和相对资金字段。market/sector 缺失时 relative 为 None，并加 quality flag，绝不填 0。

辅助分类采用 `REACTION_SIGN_V1`，只基于相对市场和相对行业连续变量的正负号，不使用 +2% 等人为幅度阈值：

- CONFIRMED_POSITIVE
- POSITIVE_EVENT_REJECTED
- NEGATIVE_EVENT_ABSORBED
- CONFIRMED_NEGATIVE
- MIXED_REACTION
- NO_CLEAR_REACTION
- INSUFFICIENT_DATA

这是 Research Classification，不是 BUY/SELL。原始连续变量和全部 Snapshot ID 均保留。

## 7. Repository

复用同一个 V3 DuckDB Repository：

- `sentiment_v3_text_analyses`：模型/规则分析缓存。
- `sentiment_v3_events`：canonical event revision 链。
- `sentiment_v3_snapshots`：News、Event Intelligence、Event Reaction Snapshot。

支持 `save_news_analysis/get_news_analysis_by_hash`、`save_event/get_event/get_event_as_of`、`save_event_reaction/get_event_reaction_as_of`。所有 as-of 都以 `available_time <= cutoff` 为核心，不按 created_at 穿越。

本地性能（同一 Windows 环境、测试 DuckDB）：1000 次 hash 10.405 ms；1000 次 Reaction 75.885 ms（0.0759 ms/次）；event save 155.792 ms、event as-of read 38.062 ms、Reaction save 122.176 ms、Reaction as-of read 44.603 ms。单条 DB 首次连接/建表成本占比较大；计算本身保持亚毫秒级。

## 8. LLM 调用统计与安全

统计 `documents_seen/cache_hits/cache_misses/qwen_calls/fallback_calls/qwen_failures/duplicate_suppressed/tokens/latency/hash_ms/cache_lookup_ms/clustering_ms`。默认最大并发 4，采用 semaphore 控制，不进行无限并发或自动重试放大。日志不记录 API key、token、cookie、Authorization header 或环境变量值。

## 9. 已知限制

- canonical event V1 是保守规则，不是全语义聚类；跨日、无共同数值事实和多实体新闻可能漏合并。
- 旧历史公告只有日期，大量记录的真实发布时间不可恢复，只能标 inferred；不应在严格历史 PIT 研究中冒充精确发布时间。
- 当前 Source Quality 只做来源类别，不做媒体排名。
- 仅支持日线/EOD 和下一交易日 Reaction；无分钟行情时不构造 OPEN/INTRADAY。
- 未接股吧或 Retail Attention；未改 DeepSeek 正式风险复核；风险事件只输出 `risk_event_candidate`。
- 未生成 News/Event/Reaction score，未赋权，未进入 S3。

## 10. 测试与隔离

S2D 新增专项覆盖 text type、event type、direction/surprise、精确/语义去重、官方确认、缓存、版本键、Qwen fallback、公告/新闻 PIT、事件时段、四类 Reaction、missing != 0、盘后下一交易日、事件 revision as-of 和 formal_weight=0。专项 31 passed。

完整 pytest：1476 passed、0 failed、0 skipped，耗时 1032.36 秒；仅有既有 Starlette/httpx 弃用警告。修改文件 Ruff：0 error。stock_report 未导入 S2D Collector，`--no-fetch --no-ai` 继续由既有隔离测试验证 0 外部金融 API、0 LLM。