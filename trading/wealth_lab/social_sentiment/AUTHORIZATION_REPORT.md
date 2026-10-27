# 金融社区数据使用权限核查报告（第一阶段交付）

核查日期：2026-10-25 · 核查人：Shixi216 + ZCode（建议用户复核后存档）
适用模块：`trading/wealth_lab/social_sentiment/`（独立库 `database/wealth_social_sentiment.duckdb`）

> **最高原则：任何未经授权的社区评论自动采集不得上线。**
> 本报告只做静态条款核查与渠道梳理；核查期间未对任何平台发起评论抓取。
> robots.txt 仅作技术立场佐证，**不构成授权证据**。

---

## 一、东方财富股吧 —— 状态：PROHIBITED（自动采集禁止）

| 核查项 | 结论 | 依据 |
|---|---|---|
| 官方服务协议 | 《东方财富用户服务协议》（2025-07 版，适用股吧） | https://about.eastmoney.com/home/protocol |
| 自动访问/采集限制 | 平台内容"未经事先书面许可，任何人不得以任何形式进行使用或创造相关作品"（第六章第1条）；禁止第三方软件/插件/系统对产品"干扰、破坏、修改"（第七章第2条） | 协议原文 |
| 正式授权 API | **无**公开评论数据 API | 协议未提供；开放接口仅覆盖行情/资讯类（quantapi.eastmoney.com 面向 Choice 付费用户） |
| 合规数据合作渠道 | **有**：东方财富 Choice（商业终端/量化 API，含舆情监测数据），需付费商务授权 | https://choice.eastmoney.com |
| 是否允许保存评论 | 未经书面许可**不允许**（第六章第1条覆盖任何形式使用） | 协议原文 |
| 是否允许 LLM 分析 | 协议未设专门 AI 条款，但"任何形式使用/衍生作品"限制覆盖 LLM 处理与衍生指标 | 第六章第1条解释 |
| 是否允许展示摘要/词频/衍生指标 | 未经书面许可**不允许**（属衍生作品） | 同上 |
| 公开/商业化系统使用 | 不允许（同上；另行情数据禁止向第三方提供/开发衍生品，第六章第2条） | 同上 |
| 第三方模型处理 | 无明示许可；风险同上 | — |
| robots 立场（佐证） | guba.eastmoney.com 无 robots.txt（404）——**缺失不等于许可** | 实测 2026-10-25 |

**结论**：自动采集/保存/LLM 分析/衍生展示在无书面授权下全部禁止。唯一合规路径为 **Choice 商业授权**（含舆情数据）；获授权前采集器保持禁用。

## 二、雪球 —— 状态：PROHIBITED（自动采集禁止，且 AI 用途被明确限制）

| 核查项 | 结论 | 依据 |
|---|---|---|
| 官方服务协议 | 《雪球服务协议》（2024-07 版）+ 社区规则 | https://xueqiu.com/about/terms 、/about/faq |
| 自动访问/采集限制 | 第三条5(2)：禁止使用任何漫游器/爬虫/抓取工具"访问、抓取、爬取、存储、缓存、检索或索引"雪球任何部分（仅合规搜索引擎留有限例外）；5(7) 禁止机器人软件等非人工方式 | 协议原文 |
| 反规避条款 | 5(3)：禁止避开/破坏安全保护与非正常浏览识别机制 | 协议原文 |
| 正式授权 API | **无**公开数据 API | 协议未提供 |
| 合规数据合作渠道 | 无公开数据合作条款；可走官方商务合作申请**书面授权** | 协议第三条第3/4款（未明示授权的权利归雪球） |
| 是否允许保存评论 | 5(2) 明文禁止存储/缓存/检索 | 协议原文 |
| 是否允许 LLM 分析 | **明确限制**：6(4) 未经同意不得将内容上传/提供到您使用的 AI；第七条未经事先同意禁止用于开发/运营 AI（含训练语料与 RAG） | 协议原文 |
| 是否允许展示摘要/词频/衍生指标 | 第三方转载需书面同意且不得实质性替代雪球服务（第七条第4款）；5(9) 禁止收费/获利性提供给第三方 | 协议原文 |
| 第三方模型处理 | 被 6(4)/第七条直接限制 | 协议原文 |
| robots 立场（佐证） | robots.txt 请求本身即被反爬拦截（302 + JS 挑战，阿里防线），技术层双重排斥 | 实测 2026-10-25 |

**结论**：雪球的采集、存储、LLM 分析（含 RAG——我们的场景正是 RAG 式分析）均被明文禁止，属四个状态中最严格的一档。除非取得雪球书面授权（商务渠道），相关功能永久禁用。

## 三、其他金融社区候选 —— 授权渠道梳理

| 来源 | 状态 | 可申请渠道 | 说明 |
|---|---|---|---|
| 深交所互动易（irm.cninfo.com.cn） | PENDING_AUTHORIZATION | 深证信数据服务平台 webapi.cninfo.com.cn（正式 API 授权申请，深交所信息公司官方） | 覆盖互动问答/公告/基金数据；申请通过并核对授权范围后方可启用 |
| Tushare Pro 互动易转授 | PENDING_AUTHORIZATION | 已有 Tushare 商用凭证（.env）；需核对 Tushare 协议对评论类数据存储/展示/LLM 处理的授权边界 | 持牌数据服务商转授路径 |
| 上证 e 互动（sns.sseinfo.com） | UNVERIFIED | 待确认（上交所信息网络公司） | 官方投资者互动平台，使用条款未完成核查，默认关闭 |
| 集思录 | UNVERIFIED | 待确认（会员 API 条款不明） | 默认关闭 |
| ~~小红书 / 抖音等泛娱乐平台~~ | 不纳入 | — | 本轮研究对象只聚焦金融社区 |

## 四、Source Authorization Registry（已落地）

注册表为**唯一采集/导入闸门**（`social_sources` 表 + `social_registry_audit` 全程审计），字段齐全：source_id / platform / terms_url / checked_at / authorized_scope / permitted_operations / retention_limit_days / authorization_evidence / status / application_channel。

- 状态机：`AUTHORIZED → PENDING_AUTHORIZATION → PROHIBITED → UNVERIFIED`；置 AUTHORIZED **必须提交授权证据**否则拒绝；
- 当前授权结论（8 源）：

| source_id | 状态 | 可执行操作 |
|---|---|---|
| eastmoney-guba | PROHIBITED | 无（采集按钮禁用，原因+Choice 渠道展示） |
| xueqiu-community | PROHIBITED | 无（LLM 用途被条款限制） |
| cninfo-irm | PENDING_AUTHORIZATION | 无（申请通道已登记） |
| tushare-irm-relay | PENDING_AUTHORIZATION | 无（待条款核对） |
| sse-einteract / jisilu-community | UNVERIFIED | 无 |
| manual-authorized-file | AUTHORIZED | import_comments / store_content / llm_analysis / display_derived（需逐批授权声明，不含商业使用） |
| synthetic-test-lab | AUTHORIZED | 联调专用，数据固定标记 synthetic_test，研究接口默认隔离 |

## 五、已授权数据源清单

**当前真实数据可用源 = 1 个**：`manual-authorized-file`（用户自备且声明已获平台授权的文件导入，留存授权声明审计，保留期 180 天）。其余全部关闭。

## 六、独立评论数据 Schema（已建库）

`social_comments`：comment_id / source_id / platform / content / origin_url / topic / symbol(池内) / parent_comment_id(回复关系) / published_at / fetched_at / content_hash / data_origin(real|synthetic_test) / authorization_ref / authorization_status / import_batch_id / analysis_status。
配套表：social_comment_analyses（LLM 结构化结果）、social_research_outputs、social_llm_calls（调用审计）、social_sources、social_registry_audit。

合规控制（均已实现并有测试）：
- **脱敏**：载荷含昵称/头像/主页/联系方式等 PII 字段 → API 层 422 拒绝 / 导入层逐条拒绝，默认不保存任何用户身份信息；
- **去重**：content_hash（源+归一正文+时间）幂等；
- **保留期**：按源 retention_limit_days 自动清理（registry.enforce_retention）；
- **合成隔离**：synthetic_test 只能进 synthetic-test-lab 源，研究/词云/热力接口默认排除，严禁冒充真实研究。

## 七、API（12 端点，`/v1/wealth-lab/social-sentiment/*`）

sources（注册表+禁用原因+申请渠道）/ import / collect（未授权→403 带原因）/ analyze（deepseek-flash，confirm）/ summary（qwen，confirm）/ research（分布/多空理由/话题/热度/分歧/变化/证据覆盖度）/ wordcloud（内置金融词典与规则切分的确定性词频，非 LLM）/ sentiment/distribution / platform-comparison / heatmap / coverage / registry/status（管理）。

## 八、验证记录

- 25 项单元测试通过（`tests/test_wealth_social_sentiment.py`）：未授权自动禁用（guba/xueqiu/cninfo/sse/jisilu 采集全部 403 且带原因）、PII 拒绝、去重、保留期清理、无真实数据时词云/分析/热力图一律 INSUFFICIENT_DATA 不编造、synthetic 隔离；
- 联调（synthetic-test-lab，明确标记非真实）：deepseek-flash 批量分类 4 条（3 判定 + 1 证据不足）→ 确定性聚合（分布 2/0/1、多空理由、分歧 0.67）→ qwen 摘要（含"样本量仅 4 条代表性极弱"诚实警示）全链路真实模型调用成功；
- **未对股吧/雪球发起任何自动评论抓取**（collect 接口全部拒绝）。

## 九、后续行动建议

1. 向深证信平台（webapi.cninfo.com.cn）提交互动易数据服务申请；
2. 咨询东方财富 Choice 商务（choice.eastmoney.com）舆情数据授权报价与范围；
3. 如需雪球数据，仅可通过雪球商务书面授权（协议 6(4)/第七条限制 AI 用途，需在授权中明确覆盖）；
4. 核对 Tushare Pro 协议对互动易问答的存储/展示/LLM 边界后决定是否启用转授源；
5. 每次授权到手后：`POST /registry/status` 提交证据变更状态 → 接入对应采集器实现 → 逐源审计。

## 参考来源

- [东方财富用户服务协议](https://about.eastmoney.com/home/protocol) / [东方财富法律声明](https://about.eastmoney.com/home/legal) / [股吧社区管理规定](https://guba.eastmoney.com/gltl.aspx) / [Choice](https://choice.eastmoney.com)
- [雪球服务协议](https://xueqiu.com/about/terms) / [雪球社区规则 FAQ](https://xueqiu.com/about/faq)
- [深证信数据服务平台](https://webapi.cninfo.com.cn) / [深交所互动易](https://irm.cninfo.com.cn)
- [同济律所：从"雪球"案看数据合规](http://www.tongjilawyer.com/index/discuss/482.html) / [君合：数据抓取的合规边界](https://www.junhe.com/legal-updates/2639)
