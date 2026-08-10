# 阶段0+阶段1 执行指令（正式保存）

来源：企业微信智能文档

---

# 阶段0：规范固化与真实盘点

不要继续询问优先级。按以下阶段执行。

本轮先完成“阶段0：规范固化与真实盘点”和“阶段1：决策基础设施”，不要跨阶段开发TradePlan、人工持仓或Paper交易。

项目：

- Hermes：E:\hermes
- Hermes-OPC：E:\hermes-opc
- 产品名称：AIQUANT-LITE
- 服务名称：Hermes-OPC

硬边界：

- 允许明确给出建仓、加仓、持有、减仓、止盈、止损、清仓、回避建议。
- 允许输出仓位比例、价格区间、交易条件和失效条件。
- 禁止连接券商、QMT、XtQuant或任何真实交易接口。
- 禁止自动发送真实委托。
- 正式策略继续保持技术60%+基本面40%+硬VETO。
- 情绪、政策、资金仍为增强层和影子验证，不得伪装为已验证正式权重。
- 不得修改或掩盖当前安装验收中尚未解决的模型链问题。
- 不得执行git commit、tag、push、reset、checkout、clean、stash或restore。
- 不得删除、覆盖或重写已有功能。

# 阶段0：规范固化与真实盘点

## 0.1 保存正式任务书

创建：

E:\hermes-opc\docs\architecture\full-permission-decision-support-v1.md

内容保存此前完整的《AIQUANT-LITE / Hermes-OPC 全权限决策支持升级任务书》。

同时创建：

E:\hermes-opc\docs\architecture\full-permission-implementation-checklist.md

检查表至少包含：

- 需求编号
- 需求内容
- 当前状态
- 已有实现文件
- 缺失内容
- 计划修改文件
- 是否需要数据库迁移
- 是否需要API修改
- 是否需要Skill修改
- 验收测试
- 最终结果

## 0.2 只读盘点

必须通过真实代码、数据库迁移、API和测试确认，不得仅凭文件名或历史报告判断。

重点核查：

- 当前用户模型
- local_user_id是否真实贯穿
- 企业微信用户绑定
- 权限服务
- 人工持仓表
- 人工成交表
- Paper账户和订单
- DecisionPacket
- TradePlan
- 正式动作枚举
- 仓位计算器
- VETO实现
- Scanner Skill
- stock_research Skill
- stock_decision Skill
- 数据新鲜度守卫
- snapshot_id和data_cutoff传递
- 后台刷新任务锁
- 实盘接口或疑似实盘连接代码

对于“已有”“部分已有”“未实现”必须分别列出证据。

## 0.3 数据库保护

修改前：

- 备份database\hermes_opc.duckdb
- 保存数据库SHA-256
- 保存数据库大小
- 保存现有migration清单
- 保存git status
- 保存现有测试基线

不得修改原始历史记录。

---

# 阶段1：决策基础设施

本阶段目标：

> 建立所有后续功能共用的用户、权限、任务上下文、动作和仓位基础，避免后续返工。


## 1.1 统一TaskContext

建立统一任务上下文对象，至少包含：

- local_user_id
- external_user_id
- channel
- session_id
- task_id
- mode
- symbol
- snapshot_id
- trade_date
- snapshot_time
- collected_at
- data_cutoff
- strategy_version
- decision_version
- created_at

external_user_id用于保存：

- 企业微信wecom_user_id
- QQ用户ID
- 桌面端本地用户ID

所有Scanner、RESEARCH、DECISION、持仓分析和组合分析必须接收统一TaskContext。

禁止各模块自行生成互不一致的cutoff或snapshot_id。

## 1.2 用户隔离基础

先建立用户隔离基础，再开发人工持仓和TradePlan。

至少实现：

- user_profiles
- external_user_bindings
- user_permissions

用户绑定结构：

external_channel + external_user_id → local_user_id → user_profile

要求：

- local_user_id作为业务数据隔离主键。
- 所有新业务表必须包含local_user_id。
- 所有Repository查询和写入必须显式传入local_user_id。
- 禁止使用“查询全部后在Python过滤”的伪隔离。
- 普通用户不能读取其他用户数据。
- 管理员权限单独控制。
- 对旧数据提供明确的legacy/default用户迁移策略。
- 不得把三名用户的数据自动归入同一用户。

本阶段不需要完成企业微信三用户正式绑定界面，但底层结构必须完成。

## 1.3 权限服务

建立统一PermissionService。

权限等级：

LEVEL_1_QUERY：

- 数据查询
- 状态查询
- 持仓读取
- 自选读取
- 决策记录读取

LEVEL_2_ANALYSIS：

- Scanner
- 个股研究
- 风险分析
- 建仓建议
- 加仓建议
- 减仓建议
- 清仓建议
- 仓位建议
- 交易计划草案

LEVEL_3_LOCAL_WRITE：

- 修改自选股
- 创建交易计划
- 更新人工持仓
- 记录人工成交
- Paper模拟交易
- 保存正式DecisionPacket

LEVEL_4_LIVE_TRADING：

- 永久禁用

要求：

- LEVEL 1和LEVEL 2无需二次确认。
- LEVEL 3需要明确授权或明确自然语言确认。
- LEVEL 4无论任何用户、模型或管理员配置都不得启用。
- 权限判断必须在服务端执行，不能只靠提示词。

## 1.4 数据状态统一

建立统一枚举：

- FRESH
- DEGRADED
- STALE
- FAILED

优先级规则：

- STALE：禁止Scanner推荐、建仓和买入建议。
- FAILED：停止正式决策。
- DEGRADED：允许分析，但降低置信度和仓位级别。
- FRESH：正常决策。

不得使用“永远最新”作为代码状态或用户输出。

## 1.5 明确动作枚举

建立统一Action枚举：

未持仓：

- STRONG_BUY：强烈建仓
- BUY：建仓
- SMALL_BUY：小仓试错
- WAIT：等待
- AVOID：回避

已持仓：

- ADD：加仓
- HOLD：持有
- REDUCE：减仓
- TAKE_PROFIT：止盈
- STOP_LOSS：止损
- EXIT：清仓

输出时使用中文，内部保存稳定英文枚举。

禁止使用以下模糊词作为最终动作：

- 建议关注
- 值得留意
- 可以看看
- 后续观察
- 自行判断
- 逢低关注

## 1.6 决策优先级

动作生成必须严格按以下顺序：

1. 实盘权限禁用检查
2. 用户权限检查
3. 数据状态检查
4. point-in-time检查
5. 硬VETO检查
6. 用户当前持仓检查
7. 正式60/40评分
8. 增强层对置信度、仓位和节奏的调整
9. 生成最终动作

硬VETO必须覆盖所有正向评分。

例如：

- 正式评分很高但触发退市风险VETO：
最终动作必须为回避或清仓。
- 数据STALE：
不得输出建仓。
- 无持仓和重仓用户：
即使分析同一股票，也必须得到不同动作。

## 1.7 动作解析器

新增确定性DecisionActionResolver。

不得完全依赖大模型自由生成动作。

输入至少包括：

- formal_score
- technical_score
- fundamental_score
- sentiment_score
- policy_score
- capital_score
- confidence
- data_status
- veto_result
- current_position_ratio
- average_cost
- current_price
- unrealized_return
- user_risk_level

输出至少包括：

- action
- action_strength
- action_reason_codes
- allow_new_position
- allow_add_position
- recommend_reduce
- recommend_exit
- confidence
- data_status
- veto_override

所有阈值必须集中配置并带版本号，不得散落在代码中。

初始阈值可按照任务书中的默认映射实现，但必须可配置、可测试、可审计。

## 1.8 仓位计算器

新增PositionSizer。

输入至少包括：

- 用户总资产
- 可用现金
- 当前单股仓位
- 当前行业仓位
- 当前组合仓位
- 股票波动率
- ATR
- 流动性
- 正式评分
- 置信度
- 数据状态
- VETO状态
- 用户风险等级
- 最大单股仓位
- 最大行业仓位

输出至少包括：

- current_position_ratio
- target_position_ratio
- position_change_ratio
- recommended_batches
- max_allowed_position
- sizing_reason_codes

默认仓位：

- 试错仓：2%至5%
- 初始仓：5%至10%
- 普通目标仓：10%至15%
- 高置信目标仓：15%至20%
- 默认单股上限：20%
- 高风险股票上限：5%

约束：

- DEGRADED至少降低一个仓位等级。
- STALE和FAILED目标新增仓位必须为0。
- VETO触发时不得新增仓位。
- 不建议杠杆。
- 不建议融资满仓。
- 不建议无限补仓。
- 不得因为亏损自动加仓摊薄成本。

## 1.9 正式评分和增强层边界

正式方向仍由：

formal_score = technical_score × 0.60

- fundamental_score × 0.40

决定。

情绪、政策和资金可以影响：

- confidence
- target_position_ratio
- recommended_batches
- entry_timing
- holding_period
- risk_level
- 是否等待确认
- 是否触发VETO

本阶段不得修改正式60/40权重。

正式输出必须分别显示：

- formal_score
- enhanced_observation
- confidence_adjustment
- position_adjustment
- consistency_status
- inconsistency_reason

不得把影子五维分数直接替换formal_score。

## 1.10 统一决策响应DTO

建立统一DecisionResponse，至少包含：

- task_context
- data_status
- action
- action_strength
- formal_score
- enhanced_score
- confidence
- veto_result
- current_position
- target_position
- position_change
- entry_guidance
- reduce_guidance
- stop_loss_guidance
- take_profit_guidance
- invalidation_conditions
- expected_holding_period
- supporting_reasons
- major_risks
- missing_data
- next_action

本阶段价格区间可以先保留结构和接口，完整TradePlan在下一阶段实现。

---

# 阶段1验收

必须新增专项测试，至少覆盖：

## 动作测试

- 强烈建仓
- 建仓
- 小仓试错
- 等待
- 回避
- 加仓
- 持有
- 减仓
- 止盈
- 止损
- 清仓

## 优先级测试

- VETO覆盖强买信号
- STALE禁止建仓
- FAILED停止决策
- DEGRADED降低仓位
- 低置信度禁止强烈建仓
- 正式60/40不被影子五维替换

## 持仓差异测试

同一股票、同一快照分别测试：

- 无持仓
- 轻仓
- 重仓
- 高成本亏损
- 低成本盈利

必须产生合理不同的动作或仓位建议。

## 用户隔离基础测试

至少建立三个测试用户：

- 用户A不能读取用户B的数据
- 用户B不能修改用户C的数据
- 管理员权限与普通用户分开
- Repository缺少local_user_id时拒绝执行
- 旧数据迁移归属明确

## 权限测试

- LEVEL 1查询允许
- LEVEL 2分析允许
- LEVEL 3未确认写入拒绝
- LEVEL 3确认后允许
- LEVEL 4永远拒绝

## 实盘禁用测试

搜索整个项目，确认不存在可执行真实订单的有效链路。

如存在疑似接口：

- 禁用
- 隔离
- 增加显式异常
- 加入测试

---

# 本轮完成报告

完成阶段0和阶段1后，只输出真实结果：

1. 保存的规范文档路径
2. 当前功能真实盘点
3. 修改文件列表
4. 数据库迁移列表
5. 新增表和字段
6. TaskContext结构
7. 用户隔离实现
8. 权限矩阵
9. Action枚举
10. 动作阈值配置
11. PositionSizer逻辑
12. DecisionResponse结构
13. VETO优先级测试
14. 数据状态测试
15. 三用户隔离基础测试
16. 实盘禁用测试
17. 新增测试数
18. 全部通过数
19. 失败测试
20. 未解决问题

禁止：

- 未完成阶段0和阶段1就开始TradePlan。
- 未完成用户隔离基础就开始人工持仓。
- 未完成专项测试就声称阶段完成。
- 因为已有同名文件就声称功能已经接通。
- 把演示输出当作端到端验收。