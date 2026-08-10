"""阶段4 验收测试：DecisionPacket版本链/决策保存/对话格式/用户绑定

运行：python -m pytest tests/test_phase4_acceptance.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.decision_support.decision_packet_v2 import (
    DecisionPacketV2Repository, create_decision_packet,
)
from trading.decision_support.decision_save_service import DecisionSaveService
from trading.decision_support.decision_response import DecisionResponse, PriceZone
from trading.decision_support.task_context import TaskContext
from trading.decision_support.conversation_formatter import (
    format_decision_response, format_simple_conclusion,
)
from trading.decision_support.user_repository import UserRepository
from trading.decision_support.permission_service import PermissionService

TZ = timezone(timedelta(hours=8))


@pytest.fixture
def temp_db(tmp_path):
    return tmp_path / "test_phase4.duckdb"


# =====================================================================
# T1 DecisionPacket 版本链
# =====================================================================
class TestDecisionPacketV2:
    def test_create_has_sha256(self):
        p = create_decision_packet(
            local_user_id="u1", symbol="600509", action="BUY",
            formal_score=0.6, confidence=0.7,
        )
        assert len(p.content_sha256) == 64
        assert p.decision_id.startswith("dec_")

    def test_frozen_immutable(self):
        """不可变：保存后修改内容会报错"""
        p = create_decision_packet(
            local_user_id="u1", symbol="600509", action="BUY",
            formal_score=0.6, confidence=0.7,
        )
        with pytest.raises(Exception):
            p.action = "SELL"  # frozen dataclass 禁止修改

    def test_save_and_get(self, temp_db):
        repo = DecisionPacketV2Repository(temp_db)
        p = create_decision_packet(
            local_user_id="u1", symbol="600509", action="BUY",
            formal_score=0.6, confidence=0.7,
        )
        repo.save(p)
        got = repo.get(p.decision_id)
        assert got is not None
        assert got.content_sha256 == p.content_sha256

    def test_no_overwrite(self, temp_db):
        """不可变：同ID重复保存拒绝"""
        repo = DecisionPacketV2Repository(temp_db)
        p = create_decision_packet(
            local_user_id="u1", symbol="600509", action="BUY",
            formal_score=0.6, confidence=0.7,
        )
        repo.save(p)
        with pytest.raises(ValueError):
            repo.save(p)

    def test_version_chain(self, temp_db):
        """版本链：新版本保留 parent_decision_id"""
        repo = DecisionPacketV2Repository(temp_db)
        p1 = create_decision_packet(
            local_user_id="u1", symbol="600509", action="BUY",
            formal_score=0.6, confidence=0.7,
        )
        repo.save(p1)
        p2 = repo.create_revision(p1, action="ADD", formal_score=0.7)
        repo.save(p2)
        assert p2.parent_decision_id == p1.decision_id
        assert p2.content_sha256 != p1.content_sha256
        versions = repo.list_versions("u1", "600509")
        assert len(versions) == 2

    def test_user_isolation(self, temp_db):
        repo = DecisionPacketV2Repository(temp_db)
        p_a = create_decision_packet(local_user_id="user_a", symbol="A", action="BUY",
                                     formal_score=0.5, confidence=0.6)
        p_b = create_decision_packet(local_user_id="user_b", symbol="B", action="HOLD",
                                     formal_score=0.3, confidence=0.6)
        repo.save(p_a)
        repo.save(p_b)
        list_a = repo.list_by_user("user_a")
        assert len(list_a) == 1
        assert list_a[0].symbol == "A"


# =====================================================================
# T2 决策保存服务（LEVEL3）
# =====================================================================
class TestDecisionSaveService:
    def _make_response(self, user="u1"):
        ctx = TaskContext(local_user_id=user, external_user_id="wx1", symbol="600509")
        return DecisionResponse(
            task_context=ctx, action="BUY", action_zh="建仓",
            formal_score=0.6, confidence=0.7,
            current_position_ratio=0.0, target_position_ratio=0.05,
            position_change_ratio=0.05, recommended_batches=1,
            snapshot_id="snap1", data_cutoff="2026-07-31",
            price_zones=PriceZone(entry_zone=[7.4, 7.6], stop_loss_price=7.14),
        )

    def test_need_confirmation(self, temp_db):
        repo = UserRepository(temp_db)
        repo.create_user("u1", "用户1")
        repo.grant_permission("u1", "LEVEL_3_LOCAL_WRITE", "admin")  # 有权限
        svc = DecisionSaveService(
            packet_repo=DecisionPacketV2Repository(temp_db),
            permission_service=PermissionService(repo),
        )
        result = svc.save_decision(self._make_response(), user_input="分析一下")
        assert result["status"] == "NEED_CONFIRMATION"

    def test_confirmed_saves(self, temp_db):
        repo = UserRepository(temp_db)
        repo.create_user("u1", "用户1")
        repo.grant_permission("u1", "LEVEL_3_LOCAL_WRITE", "admin")
        svc = DecisionSaveService(
            packet_repo=DecisionPacketV2Repository(temp_db),
            permission_service=PermissionService(repo),
        )
        result = svc.save_decision(self._make_response(), user_input="确认保存该决策")
        assert result["status"] == "SAVED"
        assert result["decision_id"].startswith("dec_")

    def test_no_level3_denied(self, temp_db):
        repo = UserRepository(temp_db)
        repo.create_user("u1", "用户1")  # 未授权LEVEL3
        svc = DecisionSaveService(
            packet_repo=DecisionPacketV2Repository(temp_db),
            permission_service=PermissionService(repo),
        )
        result = svc.save_decision(self._make_response(), user_input="确认保存该决策")
        assert result["status"] == "DENIED"

    def test_revision_chain(self, temp_db):
        repo = UserRepository(temp_db)
        repo.create_user("u1", "用户1")
        repo.grant_permission("u1", "LEVEL_3_LOCAL_WRITE", "admin")
        svc = DecisionSaveService(
            packet_repo=DecisionPacketV2Repository(temp_db),
            permission_service=PermissionService(repo),
        )
        saved = svc.save_decision(self._make_response(), user_input="确认保存该决策")
        rev = svc.create_revision(saved["decision_id"], "u1", action="ADD")
        assert rev["status"] == "REVISED"
        assert rev["parent_decision_id"] == saved["decision_id"]


# =====================================================================
# T3 对话格式
# =====================================================================
class TestConversationFormat:
    def _make_response(self):
        ctx = TaskContext(local_user_id="u1", external_user_id="wx1", symbol="600509")
        return DecisionResponse(
            task_context=ctx, action="BUY", action_zh="建仓",
            formal_score=0.6, confidence=0.72,
            current_position_ratio=0.0, target_position_ratio=0.05,
            position_change_ratio=0.05, recommended_batches=1,
            snapshot_id="snap1", data_cutoff="2026-07-31",
            price_zones=PriceZone(
                entry_zone=[7.4, 7.6], take_profit_zone=[8.0, 8.5],
                stop_loss_price=7.14, stop_loss_condition="收盘跌破7.14元",
                invalidation_condition="跌破7.14元支撑",
                expected_holding_period="2至8周",
            ),
            major_risks=["短期涨幅较大", "板块波动"],
            next_action="创建交易计划并确认建仓",
        )

    def test_format_has_action(self):
        text = format_decision_response(self._make_response())
        assert "建仓" in text
        assert "7.14" in text  # 止损价
        assert "不是自动委托" in text

    def test_simple_conclusion(self):
        text = format_simple_conclusion(self._make_response())
        assert "当前建议：建仓" in text
        assert "是否创建交易计划" in text


# =====================================================================
# T4 用户绑定初始化
# =====================================================================
class TestUserInit:
    def test_admin_and_users(self, temp_db):
        repo = UserRepository(temp_db)
        repo.create_user("u_wecom_ZhangTianYi", "管理员", "HIGH", is_admin=True)
        repo.create_user("user_1", "用户1", "MEDIUM")
        admin = repo.get_user("u_wecom_ZhangTianYi")
        assert admin.is_admin
        assert repo.resolve_local_user("wecom", "ZhangTianYi") is None  # 未绑定前

    def test_bind_wecom(self, temp_db):
        repo = UserRepository(temp_db)
        repo.create_user("user_1", "用户1")
        repo.bind_external_user("wecom", "wx_abc", "user_1")
        assert repo.resolve_local_user("wecom", "wx_abc") == "user_1"
