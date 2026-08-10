"""统一权限服务 PermissionService（阶段1.3）

权限等级：
- LEVEL_1_QUERY：数据/状态/持仓/自选/决策记录查询
- LEVEL_2_ANALYSIS：Scanner/个股研究/风险分析/建仓加仓减仓清仓建议/仓位建议/交易计划草案
- LEVEL_3_LOCAL_WRITE：修改自选/创建交易计划/更新人工持仓/记录人工成交/Paper模拟/保存DecisionPacket
- LEVEL_4_LIVE_TRADING：永久禁用

规则：
- LEVEL 1 和 LEVEL 2 无需二次确认
- LEVEL 3 需要明确授权或明确自然语言确认
- LEVEL 4 无论任何用户、模型或管理员配置都不得启用
- 权限判断必须在服务端执行，不能只靠提示词
"""
from __future__ import annotations

from enum import StrEnum

from trading.decision_support.user_repository import UserRepository


class PermissionLevel(StrEnum):
    LEVEL_1_QUERY = "LEVEL_1_QUERY"                    # 查询
    LEVEL_2_ANALYSIS = "LEVEL_2_ANALYSIS"              # 分析建议
    LEVEL_3_LOCAL_WRITE = "LEVEL_3_LOCAL_WRITE"        # 本地写入（需确认）
    LEVEL_4_LIVE_TRADING = "LEVEL_4_LIVE_TRADING"      # 实盘（永久禁用）


# 等级顺序（数值越大权限越高）
LEVEL_ORDER = {
    PermissionLevel.LEVEL_1_QUERY: 1,
    PermissionLevel.LEVEL_2_ANALYSIS: 2,
    PermissionLevel.LEVEL_3_LOCAL_WRITE: 3,
    PermissionLevel.LEVEL_4_LIVE_TRADING: 4,
}


class PermissionService:
    def __init__(self, repository: UserRepository | None = None):
        self.repository = repository or UserRepository()

    # ------------------------------------------------------------------
    # 核心检查：用户是否达到某权限等级
    # ------------------------------------------------------------------
    def can(self, local_user_id: str, level: PermissionLevel) -> bool:
        """判断用户是否具备 level 权限

        规则：
        - LEVEL 4 永久禁用
        - 管理员拥有 LEVEL 1-3
        - 所有有效用户默认拥有 LEVEL 1（查询）+ LEVEL 2（分析），无需显式授权
        - LEVEL 3 需要显式授权
        """
        # LEVEL 4 永久禁用
        if level == PermissionLevel.LEVEL_4_LIVE_TRADING:
            return False

        user = self.repository.get_user(local_user_id)
        if user is None:
            return False
        # 管理员拥有 LEVEL 1-3
        if user.is_admin:
            return True

        # 有效用户默认拥有 LEVEL 1 和 LEVEL 2（任务书：无需二次确认）
        if level in (PermissionLevel.LEVEL_1_QUERY, PermissionLevel.LEVEL_2_ANALYSIS):
            return True

        # LEVEL 3：需显式授权
        if level == PermissionLevel.LEVEL_3_LOCAL_WRITE:
            return self.repository.has_permission(local_user_id, level.value)

        return False

    # ------------------------------------------------------------------
    # 便捷检查
    # ------------------------------------------------------------------
    def can_query(self, local_user_id: str) -> bool:
        """LEVEL 1：查询（所有有效用户默认允许）"""
        user = self.repository.get_user(local_user_id)
        return user is not None

    def can_analyze(self, local_user_id: str) -> bool:
        """LEVEL 2：分析建议"""
        return self.can(local_user_id, PermissionLevel.LEVEL_2_ANALYSIS)

    def can_write_local(self, local_user_id: str) -> bool:
        """LEVEL 3：本地写入（需确认）"""
        return self.can(local_user_id, PermissionLevel.LEVEL_3_LOCAL_WRITE)

    def can_live_trade(self, local_user_id: str) -> bool:
        """LEVEL 4：实盘（永远 False）"""
        return False

    # ------------------------------------------------------------------
    # LEVEL 3 确认逻辑
    # ------------------------------------------------------------------
    CONFIRMATION_PHRASES = [
        "记录我买入了", "记录我卖出了", "记录我已经买入", "记录我已经卖出",
        "加入自选", "创建这个交易计划", "创建交易计划", "确认保存该决策",
        "paper账户买入", "paper账户卖出", "执行paper", "确认",
    ]

    def parse_natural_language_confirmation(self, text: str) -> bool:
        """用户明确说出确认短语 → 视为已授权（LEVEL 3）"""
        return any(p in text for p in self.CONFIRMATION_PHRASES)

    def require_write_confirmation(
        self, local_user_id: str, user_input: str
    ) -> bool:
        """LEVEL 3 写入前确认：用户明确授权或明确确认短语"""
        if self.repository.get_user(local_user_id) is None:
            return False
        if not self.can_write_local(local_user_id):
            return False
        return self.parse_natural_language_confirmation(user_input)
