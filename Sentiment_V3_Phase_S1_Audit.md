# AIQUANT-LITE Sentiment V3 Phase S1 审计报告

> 审计日期：2026-08-13  
> 审计对象：`E:\hermes-opc` 当前工作树、6 个指定 GitHub 仓库当前默认分支  
> 约束：本报告仅做只读审计与方案设计；所有 V3 候选因子正式权重均为 **0**。

## 0. 执行摘要

当前系统不是“一套 Sentiment V2”，而是三套并存且语义不一致的实现：

1. `trading/research/sentiment/sentiment_v2.py`：研究型四因子引擎，40/25/25/10；
2. `trading/research/sentiment/market_breadth.py`：另一套确定性市场宽度实现；
3. `stock_report_service.py -> analyze_stock.py`：`stock_report` 实际使用的新闻关键词/逐条 LLM 情绪。

四因子 V2 并未接入 `stock_report`。当前正式分仍是技术 60% + 基本面 40%；情绪、政策、资金只作为旁路输入。V3 不应再造一个未经验证的统一总分，而应保存模块向量，把 `EventIntelligence` 与 `EventReaction` 从 Sentiment 中分离。所有新模块必须经过 PIT、历史独立验证、Walk-Forward、Shadow 和真实未来观察后才能讨论非零权重。

本次口径共盘点题目列出的 **72 个候选指标**：原始字段已具备或可由现有本地历史直接计算 35 个，部分具备 32 个，完全缺失 5 个。这里的“已有”只表示数据能力，不表示已形成 PIT 安全因子，更不表示存在增量 Alpha。

---

# A. 当前 Sentiment V2

## A1. 完整调用链

### A1.1 独立四因子研究链

```text
scripts/sentiment_v2.py
  -> SentimentV2Engine.compute(symbol, data_cutoff)
       -> EventSentimentAnalyzer
            -> 本地 finance_news / announcement
            -> 可选 Tavily
            -> BatchEventExtractor -> Qwen/Longcat
       -> MarketSentimentAnalyzer -> Tushare 当日全市场 daily
       -> SectorSentimentAnalyzer -> Tushare 当前行业 + 当日 daily
       -> AttentionAnalyzer -> 本地新闻计数
       -> 四因子合成
       -> cache/sentiment_v2/{symbol}_{date}.json
```

该链没有被 `stock_report` 调用。

### A1.2 `stock_report` 实际调用链

```text
scripts/stock_report.py
  -> POST /v1/research/stock-report
  -> StockReportService.build_report
       -> [非 --no-fetch] 并发抓日线/财务/公告/新闻并落库
       -> 从本地库读取 bars / financials / news / announcements
       -> capital_flow_factor_enhanced
       -> --no-ai: sentiment_factor(news)
          否则: sentiment_factor_ai(news，最多前 6 条逐条调用模型)
       -> policy_factor(announcements)
       -> formal_score = 0.6 * technical + 0.4 * fundamental
       -> DecisionInput（情绪/资金/政策作为旁路字段）
```

### A1.3 另一套市场宽度链

`trading/research/sentiment/market_breadth.py` 从 PIT 过滤后的 canonical 日线计算上涨/下跌/平盘、板块制涨跌停、炸板、成交额变化和最高连板，并可写 `sentiment_market_snapshots`。它与四因子 V2 的 `MarketSentimentAnalyzer` 不是同一个实现；当前数据库该表为 0 行。

## A2. 四因子的真实公式

### A2.1 Event，权重 40%

输入：本地 `data_records` 中目标股票的 `finance_news` 与 `announcement`，各按 `event_time DESC LIMIT 20`；可再合并外部搜索，最终取最多 8 条。

单事件在 `BatchEventExtractor.score_events` 中的实际计算为：

```text
freshness_i = 0.5 ^ (age_hours_i / 72)
reflected_discount_i = 0.5 if price_reflected else 1.0
event_score_i = direction_i * intensity_i * confidence_i
                * source_weight_i * freshness_i * reflected_discount_i

最终 numerator 又乘了一次 source_weight：
event_score = clip(
  sum(direction * intensity * confidence * source_weight^2
      * freshness * reflected_discount)
  / sum(source_weight * freshness),
  -1, 1)

confidence = min(0.9, 0.3 + 0.1 * N + 0.2 * mean(source_weight))
```

这意味着来源权重在分子中被平方，且 `price_reflected` 在当前转换路径中基本没有可靠赋值。事件时间半衰期固定为 72 小时。

模型不可用时，底层 `SentimentEventExtractor` 的确能生成关键词结构化结果；但批处理层只接受校验状态 `VALID/REPAIRED`，`NOT_CALLED/INVALID` 的降级结果会被丢弃。因此“四因子链 Qwen 不可用自动关键词降级”在当前代码中并不成立。

### A2.2 Market，权重 25%

输入：Tushare `daily` 的当前自然日，空时退到最近交易日；没有使用传入的历史 `data_cutoff`。

```text
n = 全市场行数
breadth = (count(pct_chg > 0) - count(pct_chg < 0)) / n
limit_ratio = (count(pct_chg >= 9.8) - count(pct_chg <= -9.8)) / n
amount_t = sum(amount) / 1_000_000
amount_score = clip((amount_t - 1.0) * 0.1, -0.3, 0.3)

market_score = clip(0.5 * breadth + 0.3 * limit_ratio + amount_score, -1, 1)
confidence = 0.7 if n > 3000 else 0.5
失败时 score=0, confidence=0.3
```

问题包括：统一 9.8% 忽略 ST、创业板/科创板、北交所差异；用固定 1 万亿元锚；成交额项没有独立权重归一；历史运行会读取“今天”。

### A2.3 Sector，权重 25%

输入：Tushare 当前 `stock_basic.industry` 成分和当前日 `daily`。

```text
up_ratio = count(member_pct_chg > 0) / member_count
avg_chg = mean(member_pct_chg)
relative_strength = clip((stock_pct_chg - avg_chg) / 10, -0.5, 0.5)

sector_score = clip(
  0.6 * (up_ratio - 0.5)
  + 0.3 * (avg_chg / 10)
  + relative_strength,
  -1, 1)

confidence = 0.65 if member_count >= 10 else 0.4
```

代码还统计板块涨停数，但没有进入分数。个股相对板块强弱被混入“板块情绪”；行业成分是当前截面，不是历史成分。

### A2.4 Attention，权重 10%

输入：本地该股票全部 `finance_news.event_time`，以运行时 `now` 划分最近 7 天和此前 7 天。

```text
recent = 最近 7 天新闻数
older = 前 7 天新闻数
growth = (recent - older) / max(older, 1)
attention_score = clip(0.3 * growth, -0.5, 0.5)
confidence = 0.5
```

没有来源数、扩散速度、去重后新颖性、搜索热度或零基数抑制。它与 Event 使用相同新闻底层数据，存在重复计量。

## A3. 四因子合成、缺失、freshness、coverage

```text
base = sum(score_i * weight_i for available i) / sum(weight_i for available i)
coverage = sum(weight_i for available i)
freshness = 0.9 if event_data 存在 else 1.0
avg_confidence = mean(component_confidence_i for available i)
final = clip(base * coverage * freshness * avg_confidence, -1, 1)
```

状态：`coverage >= 0.9 -> complete`，`>= 0.5 -> partial`，否则 insufficient。

真实缺失判定是 `score == 0`，所以真正中性值会被当成缺失；失败返回的 0 分也与中性不可区分。综合 confidence 是简单平均，不按组件权重；组件数据的实际时效没有统一衰减，反而“有事件”固定乘 0.9。

## A4. `stock_report` 实际情绪公式

### 无 AI

每条新闻标题和正文匹配正负词。先命中正词设 `+1`，后命中负词会覆盖为 `-1`。

```text
freshness = 0.5 ^ (age_hours / 72)
source_weight = 1.0 for 指定权威媒体 else 0.5
item = direction * 0.5 * freshness * source_weight
score = clip(sum(item) / sqrt(news_count), -1, 1)
confidence = 0.4
```

### 有 AI

最多取前 6 条，串行逐条调用模型：

```text
item = direction * intensity * freshness * source_weight
score = clip(sum(item) / sqrt(news_count), -1, 1)
confidence = 0.6 if 任一模型成功 else 0.4
```

模型给出的 `confidence` 没有进入 item。校验状态不是 `VALID/REPAIRED` 时结果被丢弃，因此模型不可用时产生的 `NOT_CALLED` 关键词结果同样不会被采用；只有异常分支才可能回到纯关键词函数。该路径未使用四因子 V2 的 Market/Sector/Attention。

## A5. 当前数据源、性能和缓存

| 项目 | 当前事实 |
|---|---|
| 新闻 | AKShare/东方财富，落 `data_records`，当前约 1,054 条、95 只股票 |
| 公告 | AKShare/CNInfo，当前约 1,843 条、106 只股票 |
| 日线 | Tushare/本地 canonical，约 268.8 万原始日线、276.7 万 canonical 历史记录 |
| 全市场快照 | 已有 10 个 run、40,087 items；最近一次审计时已滞后，不能视为当日数据 |
| 四因子缓存 | `cache/sentiment_v2/{symbol}_{date}.json`，只按股票+日期，无输入哈希/算法版本 |
| 批事件缓存 | `trading/research/cache/sentiment/{symbol}_{date}.json`，同样缺输入版本校验 |
| `stock_report` 新闻 LLM | 没有按内容哈希复用结构化分析；重复报告可重复调用最多 6 条 |
| no-fetch/no-ai | 现有性能日志多数约 0.19–0.94 秒，情绪阶段约 0.15–0.42 毫秒 |
| 带 fetch | 现有样本总耗时约 5.47–9.70 秒；外部请求仍是主要延迟源 |

`fetch_and_persist` 已把 4 类外部抓取并行化，但 `stock_report` 仍允许按请求抓外部数据。V3 必须切断这条扩张路径。

## A6. PIT 审计

| 风险点 | 当前状态 | 结论 |
|---|---|---|
| 四因子新闻/公告查询 | 仅排序和 LIMIT，没有 `event_time/fetched_at <= cutoff` | 严重 PIT 风险 |
| Attention | 用运行时 now，不用 cutoff | 历史不可复现 |
| Market/Sector | 直接取“当前日/最近交易日” | 历史回放未来函数 |
| 行业成分 | 当前 `stock_basic` 分类 | 成分幸存/迁移偏差 |
| `stock_report` 新闻 | `load_news` 无 cutoff 过滤 | PIT 风险 |
| `stock_report` 公告 | 按 now 近 30 天，不按 report cutoff | PIT 风险 |
| 新闻表 | 有 `event_time/fetched_at/content_hash`，无规范 `available_time` | 不能严格证明可见时点 |
| 日线 | 报告仅检查 trade_date；盘中可能读取当日最终 OHLC | 必须用 finality/available_time |
| 涨跌停/炸板池 | 盘中不断变化 | 必须保存 source_timestamp 和 EOD_FINAL |
| 两融 | 通常滞后发布 | 不能把业务日期当可用时间 |
| 龙虎榜 | 收盘后发布且可能修订 | 必须按实际发布时间入 PIT |
| 板块收盘结果 | 收盘前不可完整获得 | 盘中快照与收盘最终值必须分开 |

## A7. 哪些不是 Sentiment、哪些重复

- 业绩、回购、增减持、合同、并购、诉讼、处罚、监管、ST/退市、财务异常首先是 `EventIntelligence`，不是市场情绪。
- 公告的“利好/利空方向”是事件方向，不等于投资者实际反应。
- 新闻中的分析师观点、媒体判断和股吧立场才更接近文本情绪；新闻报道的事实仍应进入 Event。
- 高开低走、尾盘跳水、放量拒绝利好属于 `StockTradingSentiment/EventReaction`。
- 成交量同时进入技术面、资金面、Market 成交额、Attention 代理，语义重叠。
- 新闻数量同时影响 Event 和 Attention；个股相对板块强弱既可作为技术相对强弱，又被放在 Sector。
- 个股/板块“主力资金”多由成交分类推导，与价格和成交量不是独立观测，需要增量检验。

## A8. 当前最严重的五个问题

1. 同名“情绪”存在三套不一致实现，四因子 V2 不是实际报告链。
2. Event 与 Sentiment 混合，公告方向被当作市场情绪。
3. 多处忽略 `data_cutoff/available_time`，历史回放存在明显 PIT 风险。
4. 缓存只按股票+日期，不含内容哈希、提示词/模型/算法版本，且 LLM 重复调用。
5. 固定阈值、重复底层数据和错误缺失判定使 coverage/confidence 失真，尚无增量验证。

---

# B. GitHub 项目对标

审计均固定到当日读取到的提交；只借指标、采集与架构思想，不复制策略、权重、固定阈值或投资结论。

## B1. Alibaba Qwen-DianJin

审计文件：[`market-sentiment-monitor/SKILL.md`](https://github.com/aliyun/qwen-dianjin/blob/fd9b47187a23f05a256d2173bf375bcbab678e46/DianJin-SKILLS/investment-researcher/market-sentiment-monitor/SKILL.md)、[`strategy-daily-briefing/SKILL.md`](https://github.com/aliyun/qwen-dianjin/blob/fd9b47187a23f05a256d2173bf375bcbab678e46/DianJin-SKILLS/investment-researcher/strategy-daily-briefing/SKILL.md)、[`market-minimal-review/SKILL.md`](https://github.com/aliyun/qwen-dianjin/blob/fd9b47187a23f05a256d2173bf375bcbab678e46/DianJin-SKILLS/investment-researcher/market-minimal-review/SKILL.md)。指定的 `quant-daily-brief` 在该提交的当前树中未找到，不能据 README 名称虚构实现。

| 项目 | 审计结论 |
|---|---|
| 值得借 | 资金/两融/龙虎榜/成交额/涨跌停/破板/波动率/板块资金/风险状态的指标目录；昨日涨停今日反馈和拥挤度视角 |
| 不值得借 | 固定金额、家数、百分比阈值；0–100 总分；直接映射仓位和交易动作 |
| 数据源 | 主要依赖 `gildata-aidata`，属于需授权的商业能力 |
| 采集频率 | Skill 描述按盘中监控、日度简报、收盘复盘组织，未提供可靠采集代码 |
| 计算方法 | 多指标规则化、阈值化、交叉验证；目标目录只有 Skill，无对应采集脚本 |
| 性能风险 | 若在报告请求内逐项拉商业接口，延迟和配额风险高 |
| 接口风险 | 商业授权、字段可得性、供应商锁定 |
| 许可证 | 仓库根及目标 Skill 路径未核实到统一 LICENSE；只借概念，复制前需单独法务核验 |
| 适配度 | 指标 taxonomy 高；数据源和固定规则低 |

## B2. FinClaw

审计文件：[`skills/a-share-board-analysis/SKILL.md`](https://github.com/aifinlab/FinClaw/blob/9e628aa802f1ac40d2c3578c13ab6e197d31ab41/skills/a-share-board-analysis/SKILL.md)。

| 项目 | 审计结论 |
|---|---|
| 值得借 | 四个 AKShare 池、炸板率、首板/连板梯队、最高板、首次封板时间、行业归属、强势股池 |
| 不值得借 | 经验阈值、情绪周期口号、由单日池直接输出买卖判断 |
| 数据源 | `stock_zt_pool_em`、`stock_zt_pool_dtgc_em`、`stock_zt_pool_zbgc_em`、`stock_zt_pool_strong_em` |
| 采集频率 | 盘中可变，必须分 intraday 与 EOD final；Skill 未实现历史归档 |
| 计算方法 | `炸板/(涨停+炸板)`；按连续涨停数构造梯队；按行业聚合 |
| 性能风险 | 单次池请求低于全市场逐股请求，但接口并发和重复抓取仍需集中调度 |
| 接口风险 | 东方财富字段变化；跌停/炸板历史窗口短；题材池有范围排除 |
| 许可证 | Apache-2.0 |
| 适配度 | 高，适合做 Snapshot 原始源和研究候选，不适合直接照抄规则 |

## B3. AKShare

审计代码：[`stock_ztb_em.py`](https://github.com/akfamily/akshare/blob/4771e6eeddf35544a6c2e4268e20239342a578cd/akshare/stock_feature/stock_ztb_em.py)、[`stock_fund_em.py`](https://github.com/akfamily/akshare/blob/4771e6eeddf35544a6c2e4268e20239342a578cd/akshare/stock_feature/stock_fund_em.py)、[`stock_lhb_em.py`](https://github.com/akfamily/akshare/blob/4771e6eeddf35544a6c2e4268e20239342a578cd/akshare/stock_feature/stock_lhb_em.py)、[`stock_hot_rank_em.py`](https://github.com/akfamily/akshare/blob/4771e6eeddf35544a6c2e4268e20239342a578cd/akshare/stock_feature/stock_hot_rank_em.py)。本机已核实 AKShare 1.18.64 接口存在，并对四类池做了只读返回字段检查。

| 项目 | 审计结论 |
|---|---|
| 值得借 | 免费/低成本聚合层；涨停、跌停、炸板、强势、昨日涨停池；行业/概念；资金流；龙虎榜；热度；新闻 |
| 不值得借 | 把封装接口视为稳定 SLA；依赖 API 自带的“主力资金”含义而不做定义审计 |
| 数据源 | 东方财富、交易所、CNInfo 等上游封装 |
| 采集频率 | 应由上游实际变化驱动；盘中池和行情多快照，资金/两融/龙虎榜按发布节奏 |
| 计算方法 | 主要提供原始表和平台口径，AIQUANT 应自行做 PIT 归档与因子计算 |
| 性能风险 | 全市场逐股调用不可接受；市场/板块批量接口可接受，需本地缓存 |
| 接口风险 | 无 SLA、字段/反爬/上游变更；部分池只支持近期约 30 个交易日；题材池说明排除 ST 和科创板 |
| 许可证 | MIT |
| 适配度 | 当前最实用的数据补齐源，但必须由本地 Snapshot 将易变接口变成历史数据 |

## B4. FinGPT

审计文件：[`FinGPT_Sentiment_Analysis_v3/README.md`](https://github.com/AI4Finance-Foundation/FinGPT/blob/f79aaa1e8c1971aa722243517b5a6efd1c4fd0a9/fingpt/FinGPT_Sentiment_Analysis_v3/README.md)。

| 项目 | 审计结论 |
|---|---|
| 值得借 | 多数据集清洗/统一、训练测试分离、weighted-F1、标签强度与解释；区分新闻和社交文本 |
| 不值得借 | 直接接入模型；把通用英文三分类直接迁移到 A 股事件；只用准确率评估投资价值 |
| 数据源 | FPB、FiQA、TFNS、NWGI 等金融新闻/推文数据集 |
| 采集频率 | 离线数据集训练评估，不是实时采集架构 |
| 计算方法 | positive/neutral/negative；部分数据集有更细强度及解释；监督微调和分类评估 |
| 性能风险 | 大模型训练/推理不是本轮需要；逐条在线推理会放大延迟 |
| 接口风险 | 数据域、语言、标签定义与 A 股公告不一致 |
| 许可证 | MIT（仍需分别遵守数据集许可证） |
| 适配度 | 标签工程和评估方法高；模型与数据直接复用低 |

三分类的核心不足：没有文本类型、事实/观点边界、事件时间和可用时间、预期差、新颖性、来源质量、影响期限，也无法表达“正面事件被市场否决”。

## B5. Diting

审计文件：[`README.md`](https://github.com/Havadking/diting/blob/8230c2ca842fe79aafffc24de4be1b2a2afbd098/README.md)、[`monitor.py`](https://github.com/Havadking/diting/blob/8230c2ca842fe79aafffc24de4be1b2a2afbd098/monitor.py)。

| 项目 | 审计结论 |
|---|---|
| 值得借 | 增量轮询、首次建立基线不报警、ID 去重、本地 state、随机退避、帖子和回复分开监控 |
| 不值得借 | 直接作为全市场股吧生产采集器；只保留 500 个 ID；串行按用户监控；非正式接口直连 |
| 数据源 | 东方财富股吧非公开 posts/replies API，偏用户动态而非股票全量流 |
| 采集频率 | 默认 60 秒并加 jitter；这是样例值，AIQUANT 不应照抄 |
| 计算方法 | `P+post_id`/`R+reply_id` 做集合去重，JSON 保存状态 |
| 性能风险 | 全市场扩展会出现 O(用户/股票) 请求膨胀；状态文件和串行循环不可扩展 |
| 接口风险 | 较高反爬、登录/字段变化、条款与隐私风险；无正式历史档案和 PIT 可见时间 |
| 许可证 | MIT |
| 适配度 | 仅适合隔离 PoC；本轮不接生产 |

## B6. stock-sentiment-cn

审计文件：[`README.md`](https://github.com/joinylee/stock-sentiment-cn/blob/main/README.md)、[`scripts/full_report_v3.py`](https://github.com/joinylee/stock-sentiment-cn/blob/main/scripts/full_report_v3.py)、[`scripts/tonghua_shun_funds.py`](https://github.com/joinylee/stock-sentiment-cn/blob/main/scripts/tonghua_shun_funds.py)。

| 项目 | 审计结论 |
|---|---|
| 值得借 | 全市场/板块共享 Snapshot 先抓一次；独立数据源顶层并发；内存 TTL 缓存；聚合完成后只调用一次 AI |
| 不值得借 | 浏览器文本抓同花顺、固定 sleep、恐贪总分和投资结论、硬编码运行假设 |
| 数据源 | 腾讯行情、东方财富/AKShare、同花顺页面、Minimax |
| 采集频率 | 示例内存 TTL 300 秒；不是经验证的生产频率 |
| 计算方法 | 市场共享数据并发；观察名单仍逐股串行抓；AI 位于聚合末端 |
| 性能风险 | 浏览器启动与页面 sleep 脆弱；观察名单扩大后逐股请求线性增长 |
| 接口风险 | 页面结构/反爬；审计代码中发现硬编码 API 凭据，属于严重安全卫生红旗，绝不能复制或执行 |
| 许可证 | README 声明 MIT；审计的根目录列表未见独立 LICENSE，复用前需复核 |
| 适配度 | Snapshot/共享缓存/AI 后置思想高；实现与投资逻辑低 |

## B7. 借鉴登记

| source_repo | source_file | borrowed_concept | why_useful | how_aiquant_differs | license |
|---|---|---|---|---|---|
| qwen-dianjin | market-sentiment-monitor/SKILL.md | 指标 taxonomy 与交叉验证 | 补足两融、龙虎榜、破板、波动风险 | 不用商业源/固定阈值/总分 | 未核实统一许可 |
| qwen-dianjin | strategy-daily-briefing/SKILL.md | 风格、行业、微观结构分层 | 避免把所有信息压成一个数 | 保存模块向量 | 未核实统一许可 |
| FinClaw | a-share-board-analysis/SKILL.md | 四类涨跌停池和连板梯队 | 直接补足投机情绪 | 本地 PIT Snapshot、无经验阈值 | Apache-2.0 |
| AKShare | stock_ztb_em.py 等 | 批量低成本数据接口 | 与当前 Provider 技术栈一致 | 自建版本、可用时间、终态归档 | MIT |
| FinGPT | Sentiment Analysis v3 | 数据集统一、标签强度、解释和评估 | 改善 Qwen schema | 加入 A 股事件、PIT、来源与预期差 | MIT |
| Diting | monitor.py | 增量、去重、基线、退避 | 降低 Attention 重复抓取 | 数据库事件流、content_hash、隔离 PoC | MIT |
| stock-sentiment-cn | full_report_v3.py | 共享 Snapshot、顶层并发、AI 后置 | 避免逐股重复抓全市场 | 后台采集，report 只读本地 | README MIT |

---

# C. 数据能力与缺口矩阵

## C1. 口径与缩写

- `已有`：本地已有历史字段或可直接从现有日线/快照确定性计算。
- `部分`：接口已存在但未稳定归档、历史不足、口径不完整或 PIT 未解决。
- `缺失`：当前 Provider/本地表没有可接受实现。
- 更新频率不写死分钟：`盘中变更`、`收盘终态`、`源发布`、`事件触发`。
- PIT：`收盘` 表示仅在收盘最终数据可用后安全；`需归档` 表示必须从现在开始保存 source/available time。
- 成本：`0本地`、`1批量`、`N逐股`；1 批量通常优于 N 逐股。

## C2. 完整矩阵

| 候选指标 | 当前已有？ | 优先数据源 | 更新频率 | PIT安全？ | 额外API | 单次成本 | 稳定性 | 推荐 |
|---|---|---|---|---|---:|---|---|---|
| 上涨家数 | 已有 | 本地全市场日线/腾讯快照 | 盘中变更+收盘终态 | 分终态后安全 | 否 | 0本地 | 高 | S2首批 |
| 下跌家数 | 已有 | 同上 | 同上 | 同上 | 否 | 0本地 | 高 | S2首批 |
| 平盘家数 | 已有 | 同上 | 同上 | 同上 | 否 | 0本地 | 高 | S2首批 |
| 涨停数 | 部分 | AKShare涨停池；备本地行情 | 盘中变更+终态 | 需归档，板块制 | 是 | 1批量 | 中 | S2首批 |
| 跌停数 | 部分 | AKShare跌停池；备本地行情 | 同上 | 需归档 | 是 | 1批量 | 中 | S2首批 |
| 炸板数 | 部分 | AKShare炸板池；备分时/高收盘 | 同上 | 需归档 | 是 | 1批量 | 中 | S2首批 |
| 炸板率 | 部分 | 涨停池+炸板池 | 同上 | 同一 cutoff 才安全 | 是 | 2批量 | 中 | 研究候选 |
| 强势股数量 | 部分 | AKShare强势股池 | 盘中变更+终态 | 需归档且口径版本化 | 是 | 1批量 | 中低 | Context候选 |
| 全市场成交额 | 已有 | 本地行情/腾讯 | 盘中变更+终态 | 终态安全 | 否 | 0本地 | 高 | S2首批 |
| 市场成交额变化 | 已有 | 本地历史日线 | 收盘终态 | 安全 | 否 | 0本地 | 高 | 研究候选 |
| 市场波动 | 已有 | 指数/全市场本地日线 | 盘中/收盘 | 对应 cutoff 安全 | 否 | 0本地 | 高 | Context/Risk |
| 站上MA20比例 | 已有 | canonical 日线 | 收盘终态 | 安全，需上市样本规则 | 否 | 0本地 | 高 | 研究候选 |
| 站上MA60比例 | 已有 | canonical 日线 | 收盘终态 | 安全，需上市样本规则 | 否 | 0本地 | 高 | 研究候选 |
| 首板数量 | 部分 | AKShare涨停池连续板字段 | 盘中变更+终态 | 需每日归档 | 是 | 1批量 | 中 | S2首批 |
| 二板数量 | 部分 | 同上 | 同上 | 同上 | 是 | 1批量 | 中 | 研究候选 |
| 三板数量 | 部分 | 同上 | 同上 | 同上 | 是 | 1批量 | 中 | 研究候选 |
| 4板+ | 部分 | 同上 | 同上 | 同上 | 是 | 1批量 | 中 | Risk/Context |
| 最高连板高度 | 部分 | 同上 | 同上 | 同上 | 是 | 1批量 | 中 | Context |
| 连板晋级率 | 部分 | 连续两日池的本地归档 | 收盘终态 | 需 t-1 冻结名单 | 是 | 1批量/日 | 中 | 研究候选 |
| 昨日涨停今日平均表现 | 部分 | 昨日池+今日本地行情 | 盘中/收盘 | 名单必须按昨日终态 | 部分 | 0/1 | 中高 | 优先研究 |
| 昨日连板今日表现 | 部分 | 昨日梯队+今日行情 | 同上 | 同上 | 部分 | 0/1 | 中高 | 优先研究 |
| 炸板修复率 | 部分 | 炸板池跨日归档+行情 | 日后观察 | 需预注册窗口 | 是 | 1批量/日 | 中 | 后续研究 |
| 高位股负反馈 | 部分 | 历史梯队+行情 | 盘中/收盘 | 定义/窗口版本化 | 部分 | 0/1 | 中 | Risk候选 |
| 板块上涨比例 | 已有 | Tushare行业成员+本地行情 | 盘中/收盘 | 历史成员需补强 | 否 | 0本地 | 中 | S2首批 |
| 板块中位涨幅 | 已有 | 同上 | 同上 | 同上 | 否 | 0本地 | 中 | 优于均值 |
| 板块成交额变化 | 已有 | 行业映射+本地历史 | 收盘 | 历史成员需版本化 | 否 | 0本地 | 中 | 研究候选 |
| 板块涨停数量 | 部分 | 涨停池行业字段+成员映射 | 盘中/收盘 | 需归档和分类版本 | 是 | 1批量 | 中 | 研究候选 |
| 板块炸板率 | 部分 | 涨停/炸板池行业字段 | 同上 | 同上 | 是 | 2批量 | 中低 | Context候选 |
| 板块资金流 | 部分 | AKShare行业/概念资金流 | 源发布 | 需归档且口径审计 | 是 | 1批量 | 中低 | 低优先验证 |
| 龙头强度 | 部分 | 本地板块成员行情/池 | 盘中/收盘 | 龙头定义版本化 | 否 | 0本地 | 中 | 研究候选 |
| 龙头跟随度 | 部分 | 同上 | 同上 | 先冻结龙头名单 | 否 | 0本地 | 中 | 研究候选 |
| 个股相对板块强弱 | 已有 | 本地行情+行业映射 | 盘中/收盘 | 历史行业需版本化 | 否 | 0本地 | 中高 | Stock模块 |
| 个股资金流 | 部分 | 现有 capital/AKShare | 源发布 | 需归档，供应商口径 | 是 | N逐股 | 中低 | 低频/按需 |
| 板块资金流 | 部分 | AKShare批量板块资金 | 源发布 | 需归档 | 是 | 1批量 | 中低 | 研究候选 |
| 全市场资金流 | 部分 | AKShare市场资金流 | 源发布 | 需归档 | 是 | 1批量 | 中低 | Context |
| 融资余额 | 部分 | Tushare/交易所/AKShare | 源发布 | 按 available_time | 是 | 1批量 | 中高 | S2低频 |
| 融资买入额 | 部分 | 同上 | 源发布 | 同上 | 是 | 1批量 | 中高 | 研究候选 |
| 融资偿还 | 部分 | 同上 | 源发布 | 同上 | 是 | 1批量 | 中高 | 研究候选 |
| 龙虎榜机构资金 | 部分 | Tushare top_list/AKShare LHB | 收盘后发布 | 按 available_time | 是 | 1批量 | 中 | Event/Capital |
| 游资行为 | 缺失 | 候选 LHB 席位识别 | 收盘后 | 身份映射需审计 | 是 | 1批量 | 低 | 暂不生产 |
| 大单/主力资金 | 部分 | AKShare/东方财富口径 | 源发布 | 需口径版本和归档 | 是 | 1或N | 低 | 谨慎/可能拒绝 |
| 个股涨跌幅 | 已有 | 本地日线/腾讯 | 盘中/收盘 | cutoff 对齐 | 否 | 0本地 | 高 | 基础字段 |
| 换手率 | 已有 | Tushare/腾讯/AKShare | 盘中/收盘 | cutoff 对齐 | 否 | 0本地 | 高 | 研究候选 |
| 成交量变化 | 已有 | 本地历史 | 盘中/收盘 | cutoff 对齐 | 否 | 0本地 | 高 | 注意与技术重复 |
| 量比 | 已有 | 腾讯/AKShare或本地估算 | 盘中 | 需统一定义 | 否 | 0本地 | 中高 | Context候选 |
| 振幅 | 已有 | OHLC | 盘中/收盘 | cutoff 对齐 | 否 | 0本地 | 高 | Risk候选 |
| 收盘位置 | 已有 | `(C-L)/(H-L)` | 收盘 | 安全 | 否 | 0本地 | 高 | 优先研究 |
| 高开低走 | 已有 | OHLC | 盘中/收盘 | 需 session finality | 否 | 0本地 | 高 | EventReaction |
| 低开高走 | 已有 | OHLC | 盘中/收盘 | 同上 | 否 | 0本地 | 高 | EventReaction |
| 尾盘变化 | 部分 | 分钟线/实时快照序列 | 尾盘/收盘 | 需固定观测窗 | 部分 | 0/N | 中 | 后续补齐 |
| 个股相对市场 | 已有 | 本地个股+基准 | 盘中/收盘 | 同 cutoff | 否 | 0本地 | 高 | 优先研究 |
| 个股相对行业 | 已有 | 本地行情+行业 | 盘中/收盘 | 历史成员版本化 | 否 | 0本地 | 中高 | 优先研究 |
| 新闻数量 | 已有 | 本地 finance_news | 事件触发 | 当前 available_time 不足 | 否 | 0本地 | 中 | Attention，不定方向 |
| 新闻数量变化 | 已有 | 本地新闻历史 | 事件触发/日汇总 | 需 cutoff 重算 | 否 | 0本地 | 中 | 研究候选 |
| 新闻来源数量 | 已有 | publisher/source 字段 | 事件触发 | 需来源规范化 | 否 | 0本地 | 中 | 研究候选 |
| 新闻扩散速度 | 部分 | 本地聚类+首见时间 | 事件触发 | 需 available_time/聚类版本 | 否 | 0本地 | 中 | S2后段 |
| 股吧发帖 | 缺失 | 隔离 PoC：东财股吧 | 事件流 | 高反爬/PIT需自建 | 是 | N逐股 | 低 | 暂缓 |
| 股吧评论 | 缺失 | 隔离 PoC：东财股吧 | 事件流 | 同上 | 是 | N逐股 | 低 | 暂缓 |
| 搜索热度 | 部分 | AKShare东财热榜/历史 | 源发布 | 需归档，非真实搜索量 | 是 | 1批量 | 中低 | Proxy-only |
| 其他注意力Proxy | 部分 | 热榜、成交/换手异常、新闻聚类 | 源发布 | 分清与交易因子重复 | 部分 | 0/1 | 中 | Context候选 |
| 业绩 | 已有 | 公告/财务报表 | 事件触发 | 按 available_time | 否 | 0本地 | 高 | Event |
| 回购 | 已有 | 公告 | 事件触发 | 同上 | 否 | 0本地 | 高 | Event |
| 增持 | 已有 | 公告 | 事件触发 | 同上 | 否 | 0本地 | 高 | Event |
| 减持 | 已有 | 公告 | 事件触发 | 同上 | 否 | 0本地 | 高 | Event/Risk |
| 合同 | 已有 | 公告 | 事件触发 | 同上 | 否 | 0本地 | 高 | Event |
| 并购 | 已有 | 公告/新闻 | 事件触发 | 同上 | 否 | 0本地 | 中高 | Event |
| 诉讼 | 已有 | 公告 | 事件触发 | 同上 | 否 | 0本地 | 高 | Event/Risk |
| 处罚 | 已有 | 公告/监管新闻 | 事件触发 | 同上 | 否 | 0本地 | 高 | Risk |
| 监管 | 已有 | 公告/监管新闻 | 事件触发 | 同上 | 否 | 0本地 | 中高 | Risk |
| ST/退市 | 缺失 | 交易所状态/公告专门表 | 事件触发 | 状态生效时点关键 | 是 | 1批量 | 中高 | S2补齐 |
| 财务异常 | 缺失 | 财报结构化规则+审计意见 | 报告发布 | 按 available_time | 部分 | 0/1 | 中 | 独立 Risk 模块 |
| 政策事件 | 已有 | 新闻/公告/现有 policy | 事件触发 | 需作用对象和生效时点 | 否 | 0本地 | 中 | Event，不是情绪 |

### C3. 汇总

| 分类 | 已有 | 部分 | 缺失 | 合计 |
|---|---:|---:|---:|---:|
| Market Breadth | 8 | 5 | 0 | 13 |
| Speculation | 0 | 10 | 0 | 10 |
| Sector | 4 | 5 | 0 | 9 |
| Capital | 0 | 8 | 1 | 9 |
| Stock Trading | 10 | 1 | 0 | 11 |
| Attention | 3 | 3 | 2 | 8 |
| Event | 10 | 0 | 2 | 12 |
| **总计** | **35** | **32** | **5** | **72** |

可由现有 AKShare/Tushare/腾讯直接补齐或显著改善的，是涨跌停/炸板/强势池、连板梯队、板块行情、资金流、两融、龙虎榜、热榜、分钟/实时行情。需要新源或专门采集的主要是生产级股吧帖子/评论、可靠游资身份、完整 ST/退市状态时序；财务异常更多需要新规则和结构化字段，不一定需要商业源。

不建议接：Qwen-DianJin 的商业 `gildata-aidata`（本轮未经成本/授权评估）、非公开股吧全市场爬虫、浏览器抓同花顺页面、来源不明的“主力/大单”商业黑箱、任何带硬编码凭据的上游实现。

---

# D. Sentiment V3 建议结构

## D1. 模块边界

```text
SentimentV3Snapshot
├─ MarketBreadth            全市场广度、成交、趋势参与率
├─ SpeculationSentiment     涨跌停、炸板、连板梯队及次日反馈
├─ SectorSentiment          板块扩散、涨停/炸板、龙头与跟随
├─ CapitalSentiment         资金流、两融、龙虎榜（保留供应商口径）
├─ StockTradingSentiment    个股 K 线反应、相对强弱、尾盘行为
├─ RetailAttention          注意力强度，不默认等于方向
└─ NewsSentiment            观点/预期/市场反应文本的方向与强度

独立对象：
EventIntelligence           事实事件、风险事件、政策事件
EventReaction               事件发生后市场是否确认、拒绝或吸收
```

不建议保留生产“统一情绪总分”。研究库可以保留一个明确标记 `RESEARCH_ONLY`、带算法版本的临时组合用于比较，但默认输出应是模块向量、质量、覆盖率和证据；所有组合权重为 0，不能进入 A/B/Canonical。

## D2. EventReaction

反应时钟从 `available_time` 开始，而不是公告落款日或新闻事件日。价格反应必须使用相对市场/行业的异常收益、成交量/换手异常、开盘缺口、收盘位置，并按盘前、盘中、盘后事件选择可交易的首个观察窗口。

```text
正面 Event + 正面异常价格/成交确认 -> CONFIRMED_POSITIVE
正面 Event + 负面异常反应          -> POSITIVE_EVENT_REJECTED
负面 Event + 相对抗跌/修复         -> NEGATIVE_EVENT_ABSORBED
负面 Event + 放量异常下跌          -> CONFIRMED_NEGATIVE
证据不足/方向冲突                  -> NO_CLEAR_REACTION / MIXED_REACTION
```

阈值不在 S1 写死；S3 只用预注册、滚动分位数或横截面标准化候选进行检验。

## D3. Qwen 新标签候选

```yaml
text_id: stable content hash
text_type: FACT_EVENT | EXPECTATION_CHANGE | MARKET_REACTION | OPINION | RISK | RUMOR | OTHER
event_type: controlled taxonomy
entities: [company, symbol, sector, regulator, person]
direction: POSITIVE | NEGATIVE | NEUTRAL | MIXED
intensity: 0..1
confidence: 0..1
horizon: INTRADAY | SHORT | MEDIUM | LONG
novelty: 0..1
surprise: -1..1
event_time: timestamp | null
available_time: timestamp
source_quality: OFFICIAL | LICENSED_MEDIA | MEDIA | SOCIAL | UNKNOWN
fact_status: CONFIRMED | REPORTED | RUMOR | DISPUTED | RETRACTED
affected_scope: symbol | sector | market
evidence_spans: [...]
summary: concise factual summary
schema_version: string
prompt_version: string
model_route_version: string
```

规则：

- 公告默认进入 `FACT_EVENT` 或 `RISK`，不直接进入 `NewsSentiment`。
- `direction` 表示文本/事件语义方向，不表示未来收益预测。
- `MARKET_REACTION` 必须引用实际盘面或市场参与者反应；纯事实新闻不能冒充反应。
- `RUMOR` 与确认事实隔离；修正/撤稿建立 supersedes 关系。
- confidence 必须绑定证据和来源，不再只用“模型有没有返回”。

---

# E. 数据获取、Snapshot 与增量计算

## E1. 硬架构

```text
后台采集器
  -> RawSnapshot（保留原响应/字段/源时间）
  -> CanonicalSnapshot（标准化、PIT、质量与终态）
  -> ModuleFeatureSnapshot（确定性计算）
  -> Research Shadow / S3 数据集

stock_report
  -> 只读取 data_cutoff 之前最新的本地 FINAL/合格快照
  -> 不临时抓全市场/板块/股吧
  -> 不临时调用 LLM
```

### 建议频率原则

| 数据类 | 调度原则 | 原因 |
|---|---|---|
| 行情/市场宽度/涨跌停/炸板/板块行情 | 交易时段按源变化和限流做增量快照，并强制收盘终态 | 变化快，盘中值不是终值 |
| 板块/个股资金、新闻增量、热榜 | 按上游刷新节奏和内容变化触发 | 避免无变化轮询和逐股风暴 |
| 两融、龙虎榜、历史汇总 | 按正式发布时间抓取，允许更正快照 | 业务日期早于可见时间 |
| 新公告/新新闻 | 新 content_hash 事件触发结构化分析 | 只处理新增或版本变化 |

具体分钟数必须在 S2 用接口观测、限流和负载实验确定，不在 S1 固定。

## E2. Snapshot 数据契约

每个 Snapshot 至少包含：

```text
snapshot_id
module / metric_set
schema_version / algorithm_version
provider / source_endpoint / source_request_id
source_timestamp
fetched_at
available_time
data_cutoff
snapshot_timestamp
trade_date / session
finality = INTRADAY | EOD_FINAL | CORRECTED
universe_version / classification_version
coverage / completeness / missing_reason
raw_content_hash / normalized_content_hash
previous_snapshot_id / supersedes_snapshot_id
quality_flags / PIT_flags
records_or_evidence
```

读取规则：`available_time <= report_cutoff`，优先同一 session 的最新合法版本；禁止用未来 `CORRECTED` 覆盖历史回放当时可见的旧版本。

## E3. 文本增量计算

稳定哈希不能使用 Python `hash()`。建议对规范化后的 `source + source_id/url + publisher + published_at + title + content` 做 SHA-256，并保留内容版本关系。

```text
新记录
 -> canonical content_hash 是否存在？
    -> 是：查 (content_hash, schema_version, prompt_version, model_route_version)
            -> 命中则直接复用
            -> 版本变化才重算
    -> 否：Qwen 结构化 -> 保存结果、证据、用量、延迟和校验状态
```

报告端只读已完成结果；未处理时标记缺失，不同步等待模型。相较当前每次 `stock_report` 最多重复调用 6 条、独立 V2 最多处理 8 条，新结构在没有新内容时模型调用量接近 0；实际降幅需用 S2 调用日志测量，但可以确定会显著下降。

## E4. 数据源计划摘要

| 模块 | 优先源 | 备用源 | PIT/缓存 | 性能预估 |
|---|---|---|---|---|
| Market | 本地全市场快照/腾讯 | Tushare/AKShare | session+finality 快照 | 1 次批量，低 |
| Speculation | AKShare四类池 | 本地行情重建 | 每次源时间+EOD final | 4–5 次批量，中低 |
| Sector | 本地行业映射+行情 | AKShare板块 | 分类版本+快照 | 本地计算为主，低 |
| Capital | Tushare/AKShare | 交易所接口 | 按发布时间，不逐报告抓 | 批量中；逐股高 |
| Stock | 本地 OHLCV/实时快照 | 腾讯 | 同 cutoff 确定性计算 | 0 外部，低 |
| Attention | 本地新闻/热榜 | 隔离股吧 PoC | content hash/事件流 | 新闻低；股吧高 |
| News | 本地文本+Qwen异步 | 关键词结构化降级 | 处理键版本化 | 仅新增文本调用 |
| Event | 公告/新闻/财务 | 交易所/CNInfo | available_time+更正链 | 事件驱动 |
| EventReaction | Event+本地行情 | 无需新源 | 事件锚点+观察窗版本 | 本地计算，低 |

---

# F. Phase S2 建议

S2 只建设数据层、Snapshot 层、研究 Shadow 层：

1. 定义新表和数据契约：`available_time/finality/version/hash/supersedes`，不改正式读链。
2. 建市场与涨跌停池后台采集器，先完成 Raw/EOD Snapshot、覆盖率和源对账。
3. 补 PIT 规则：交易日历、板块制涨跌停、ST/上市状态、历史行业成员、延迟发布和更正。
4. 实现确定性模块：Market、Speculation、Sector、Stock；只写研究快照。
5. 接 Capital 的低频批量快照，保留供应商原始口径，不把“主力资金”当真值。
6. 改文本增量键和 Qwen schema；公告写 Event，观点/反应写 News；所有调用异步。
7. 实现 EventReaction 数据集，严格锚定 available_time 和首个可交易窗口。
8. 新建独立 `Sentiment V3 Research Shadow` 输出与监控；不接 B Shadow、Canonical 或 DecisionEngine。
9. 做断点续抓、接口降级、内容更正、覆盖/延迟/成本 dashboard 和历史回填可行性报告。

开发顺序优先“本地已有且 PIT 容易”的 Market/Stock，再做需要每日归档的 Speculation/Sector，再做 Capital/Text/Attention。股吧只允许隔离 PoC，不能成为 S2 主路径。

---

# G. Phase S3 研究计划

## G1. 固定基准与单因子增量

冻结 `Baseline = 当前正式 A`，不重调 A。依次单独测试：

```text
Baseline + Market Breadth
Baseline + Speculation Sentiment
Baseline + Sector Sentiment
Baseline + Capital Sentiment
Baseline + Stock Trading Sentiment
Baseline + News Sentiment
Baseline + Event Reaction
```

不得先把多个弱因子组合成“漂亮总分”。只有单模块通过后，才可研究低相关组合；仍保持正式权重 0。

## G2. 验证顺序

1. 数据契约/PIT 单元审计：`available_time`、交易时段、终态、更正、退市样本、历史行业成员。
2. 冻结因子定义、方向、缺失逻辑、winsorize/标准化规则和评估方案。
3. 开发集只用于发现问题；独立时间段和未参与设计的股票作为历史独立验证。
4. 逐日/逐期 Walk-Forward，训练窗口只看过去；有标签重叠时使用 purge/embargo。
5. 横截面 IC、RankIC、稳定性、衰减和换手；分位数组、Top10、Top20。
6. 1/3/5/10/20 日平均/中位收益、胜率、Profit Factor、MAE、MFE、最大回撤。
7. 去除 Top10% 赢家后复算，防止结果由少数涨停股支撑。
8. 分牛/熊/震荡、大盘/小盘、行业、流动性和事件类型检验。
9. 加交易成本、停牌/涨跌停不可成交、公告盘后可交易时点和容量约束。
10. 控制多重检验和选择偏差；保留全部尝试、版本和失败结果。
11. 进入独立 Research Shadow，记录实时缺失、延迟、漂移、预测与未来收益。
12. 经过真实未来观察后，才决定是否发起生产变更评审。

## G3. 因子归宿

每个因子只能被归入：

```text
ALPHA_CANDIDATE
CONTEXT_ONLY
CONFIDENCE_ONLY
RISK_ONLY
VETO_ONLY
MONITOR_ONLY
REJECTED
```

判断依据必须是样本外增量、稳定性、成本、PIT 可证明性和未来 Shadow 表现，而不是指标“看起来合理”。`VETO_ONLY` 还需单独证明减少尾部损失且不会产生不可接受的机会损失。

---

# H. 安全与影响确认

- 本轮只新增本审计 Markdown；未修改任何生产代码、配置、模型、阈值或数据库。
- 未修改 A 正式策略、60/40、DecisionEngine、PositionSizer、PriceZone 或 Scanner。
- 未修改 B Shadow、B-OVERHEAT-1.0.0 或任何 B 参数。
- 未修改或影响 Canonical Shadow。
- 未连接券商，未产生订单、成交或持仓变化。
- 未修改 `.env`，未写入 API Key；未执行发现于上游仓库的凭据。
- 未执行 commit、push、reset、restore、clean、checkout、stash 或其他 Git 写操作。

## 附：审计范围说明

用户描述当前主要分支为 `sentiment`，但审计时本地实际 `git branch --show-current` 为 `main`，且工作树在本轮前已有大量未提交改动。本报告基于当前工作树事实，不切换分支、不清理、不覆盖既有改动。
