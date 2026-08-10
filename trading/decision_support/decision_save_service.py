"""决策保存服务（阶段4.2）— 保存正式 DecisionPacket

- LEVEL3 权限：需用户确认
- 不可变：保存后不可修改，修改走版本链
- 从 DecisionResponse 生成决策包
"""
from __future__ import annotations

from trading.decision_support.decision_packet_v2 import (
    DecisionPacketV2, DecisionPacketV2Repository, create_decision_packet,
)
from trading.decision_support.decision_response import DecisionResponse
from trading.decision_support.permission_service import PermissionService
from trading.decision_support.task_context import TaskContext


class DecisionSaveService:
    def __init__(
        self,
        packet_repo: DecisionPacketV2Repository | None = None,
        permission_service: PermissionService | None = None,
    ):
        self.packet_repo = packet_repo or DecisionPacketV2Repository()
        self.permissions = permission_service or PermissionService()

    def save_decision(
        self,
        response: DecisionResponse,
        user_input: str = "",
        *,
        confirmed: bool = False,
    ) -> dict:
        """保存正式决策（LEVEL3 需确认）

        confirmed=True 或用户输入含确认短语 → 允许保存
        """
        ctx: TaskContext = response.task_context
        if ctx is None:
            raise ValueError("决策响应缺少任务上下文")

        # LEVEL3 权限检查
        if not self.permissions.can_write_local(ctx.local_user_id):
            return {
                "status": "DENIED",
                "reason": "用户无LEVEL3写入权限（需管理员授权）",
            }
        if not confirmed and not self.permissions.parse_natural_language_confirmation(user_input):
            return {
                "status": "NEED_CONFIRMATION",
                "reason": "保存正式决策需用户明确确认",
                "next": "请回复确认（如：确认保存该决策）",
            }

        # 生成决策包（兼容 action 为枚举或字符串）
        action_value = response.action.value if hasattr(response.action, "value") else str(response.action)
        packet = create_decision_packet(
            local_user_id=ctx.local_user_id,
            symbol=ctx.symbol,
            action=action_value,
            formal_score=response.formal_score,
            confidence=response.confidence,
            position_advice={
                "current_ratio": response.current_position_ratio,
                "target_ratio": response.target_position_ratio,
                "change_ratio": response.position_change_ratio,
                "batches": response.recommended_batches,
            },
            snapshot_id=response.snapshot_id,
            data_cutoff=response.data_cutoff,
            enhanced_score=response.enhanced_score,
            veto_result={
                "veto_triggered": response.veto_triggered,
                "action_override": response.action_zh,
            },
        )
        self.packet_repo.save(packet)
        return {
            "status": "SAVED",
            "decision_id": packet.decision_id,
            "content_sha256": packet.content_sha256,
            "action": packet.action,
            "created_at": packet.created_at,
        }

    def create_revision(self, parent_id: str, local_user_id: str, **changes) -> dict:
        """创建决策新版本（版本链）"""
        parent = self.packet_repo.get(parent_id)
        if parent is None:
            return {"status": "NOT_FOUND", "reason": f"决策 {parent_id} 不存在"}
        if parent.local_user_id != local_user_id:
            return {"status": "DENIED", "reason": "不能修改其他用户的决策"}
        new_packet = self.packet_repo.create_revision(parent, **changes)
        self.packet_repo.save(new_packet)
        return {
            "status": "REVISED",
            "decision_id": new_packet.decision_id,
            "parent_decision_id": parent_id,
            "content_sha256": new_packet.content_sha256,
        }
