"""Wealth Lab v3.3：三模型协作受控 AI 研究系统。

固定角色映射（不可自行更换）：
  PLANNER   → DeepSeek V4 Flash（实验模式，规划，默认未配置 → MODEL_UNAVAILABLE）
  RESEARCHER → Qwen 3.8 Max 0902（金融研究）
  REVIEWER   → DeepSeek V4 Pro（独立复核）

模式：
  STABLE_RESEARCH（默认）：数据→策略→VETO→Qwen→Pro→数值核验→决策包（≤2 次调用）
  EXPERIMENTAL_AI_RESEARCH（Feature Flag）：Flash 规划→校验→用户确认→实验(≤3)→
  VETO→Qwen→Pro→决策包（≤3 次调用）

状态机：PLANNING→VALIDATION→APPROVAL→BACKTESTING→HARD_RISK_CHECK→RESEARCH→
REVIEW→VERIFICATION→COMPLETED；任何环节失败记录原因并终止。
"""
from trading.wealth_lab.research.roles import ROLE_SPECS, RoleSpec

__all__ = ["ROLE_SPECS", "RoleSpec"]
