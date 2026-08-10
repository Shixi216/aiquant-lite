"""用户隔离基础（阶段1.2）

- user_profiles：用户档案（风险等级/资产/仓位上限）
- external_user_bindings：外部渠道绑定（wecom/qq/desktop → local_user_id）
- user_permissions：用户权限

绑定结构：external_channel + external_user_id → local_user_id → user_profile

要求：
- local_user_id 作为业务数据隔离主键
- 所有新业务表必须包含 local_user_id
- Repository 查询写入必须显式传入 local_user_id
- 禁止"查询全部后在 Python 过滤"的伪隔离
- 普通用户不能读取其他用户数据
- 旧数据提供 legacy/default 用户迁移策略
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

TZ = timezone(timedelta(hours=8))

# 旧数据默认归属用户（迁移策略）
LEGACY_USER_ID = "legacy_default"


@dataclass
class UserProfile:
    local_user_id: str
    display_name: str
    risk_level: str = "MEDIUM"        # LOW / MEDIUM / HIGH
    total_assets: float = 0.0
    max_single_position: float = 0.20
    max_sector_position: float = 0.35
    is_admin: bool = False


class UserRepository:
    """用户档案 + 绑定 + 权限 的数据访问层（显式 local_user_id）"""

    def __init__(self, db_path: Path | None = None):
        from config.settings import settings
        if db_path is None:
            db_path = Path(settings.opc_database_path)
            if not db_path.is_absolute():
                db_path = Path(__file__).resolve().parents[2] / db_path
        self.db_path = db_path

    def _connect(self):
        from database.db import open_database
        con = open_database(self.db_path)
        # 确保表存在（自举建表，兼容测试临时库）
        self._ensure_schema(con)
        return con

    def _ensure_schema(self, con) -> None:
        """确保三张用户表存在（幂等）"""
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                local_user_id VARCHAR PRIMARY KEY,
                display_name VARCHAR NOT NULL,
                risk_level VARCHAR NOT NULL DEFAULT 'MEDIUM',
                total_assets DOUBLE DEFAULT 0,
                max_single_position DOUBLE DEFAULT 0.20,
                max_sector_position DOUBLE DEFAULT 0.35,
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS external_user_bindings (
                binding_id VARCHAR PRIMARY KEY,
                external_channel VARCHAR NOT NULL,
                external_user_id VARCHAR NOT NULL,
                local_user_id VARCHAR NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (external_channel, external_user_id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_permissions (
                permission_id VARCHAR PRIMARY KEY,
                local_user_id VARCHAR NOT NULL,
                permission_level VARCHAR NOT NULL,
                granted_by VARCHAR NOT NULL,
                granted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                revoked_at TIMESTAMP WITH TIME ZONE,
                UNIQUE (local_user_id, permission_level)
            )
        """)

    # ------------------------------------------------------------------
    # 用户档案
    # ------------------------------------------------------------------
    def create_user(
        self,
        local_user_id: str,
        display_name: str,
        risk_level: str = "MEDIUM",
        total_assets: float = 0.0,
        is_admin: bool = False,
    ) -> UserProfile:
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO user_profiles
                (local_user_id, display_name, risk_level, total_assets, is_admin)
                VALUES (?, ?, ?, ?, ?)
                """,
                [local_user_id, display_name, risk_level, total_assets, is_admin],
            )
        finally:
            con.close()
        return UserProfile(
            local_user_id=local_user_id, display_name=display_name,
            risk_level=risk_level, total_assets=total_assets, is_admin=is_admin,
        )

    def get_user(self, local_user_id: str) -> Optional[UserProfile]:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT local_user_id, display_name, risk_level, total_assets, "
                "max_single_position, max_sector_position, is_admin "
                "FROM user_profiles WHERE local_user_id=?",
                [local_user_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return UserProfile(
            local_user_id=row[0], display_name=row[1], risk_level=row[2],
            total_assets=row[3], max_single_position=row[4],
            max_sector_position=row[5], is_admin=row[6],
        )

    def update_assets(self, local_user_id: str, total_assets: float) -> None:
        con = self._connect()
        try:
            con.execute(
                "UPDATE user_profiles SET total_assets=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE local_user_id=?",
                [total_assets, local_user_id],
            )
        finally:
            con.close()

    # ------------------------------------------------------------------
    # 外部绑定
    # ------------------------------------------------------------------
    def bind_external_user(
        self, external_channel: str, external_user_id: str, local_user_id: str
    ) -> str:
        """绑定外部用户 → local_user_id"""
        binding_id = f"bind_{uuid.uuid4().hex[:16]}"
        con = self._connect()
        try:
            # 检查是否已绑定
            existing = con.execute(
                "SELECT binding_id FROM external_user_bindings "
                "WHERE external_channel=? AND external_user_id=?",
                [external_channel, external_user_id],
            ).fetchone()
            if existing:
                # 更新绑定目标
                con.execute(
                    "UPDATE external_user_bindings SET local_user_id=? WHERE binding_id=?",
                    [local_user_id, existing[0]],
                )
                return existing[0]
            con.execute(
                """
                INSERT INTO external_user_bindings
                (binding_id, external_channel, external_user_id, local_user_id)
                VALUES (?, ?, ?, ?)
                """,
                [binding_id, external_channel, external_user_id, local_user_id],
            )
        finally:
            con.close()
        return binding_id

    def resolve_local_user(
        self, external_channel: str, external_user_id: str
    ) -> Optional[str]:
        """外部用户 → local_user_id（不存在返回 None）"""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT local_user_id FROM external_user_bindings "
                "WHERE external_channel=? AND external_user_id=?",
                [external_channel, external_user_id],
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else None

    def resolve_or_create(
        self, external_channel: str, external_user_id: str,
        display_name: str = "", risk_level: str = "MEDIUM",
    ) -> str:
        """解析外部用户，不存在则自动创建（默认普通用户）"""
        local_id = self.resolve_local_user(external_channel, external_user_id)
        if local_id:
            return local_id
        # 自动创建
        local_id = f"u_{external_channel}_{external_user_id}"
        if self.get_user(local_id) is None:
            self.create_user(local_id, display_name or external_user_id, risk_level)
        self.bind_external_user(external_channel, external_user_id, local_id)
        return local_id

    # ------------------------------------------------------------------
    # 权限
    # ------------------------------------------------------------------
    def grant_permission(
        self, local_user_id: str, permission_level: str, granted_by: str
    ) -> None:
        con = self._connect()
        try:
            # 先撤销旧授权（保持唯一）
            con.execute(
                "UPDATE user_permissions SET revoked_at=CURRENT_TIMESTAMP "
                "WHERE local_user_id=? AND permission_level=? AND revoked_at IS NULL",
                [local_user_id, permission_level],
            )
            con.execute(
                """
                INSERT INTO user_permissions
                (permission_id, local_user_id, permission_level, granted_by)
                VALUES (?, ?, ?, ?)
                """,
                [f"perm_{uuid.uuid4().hex[:16]}", local_user_id, permission_level, granted_by],
            )
        finally:
            con.close()

    def revoke_permission(self, local_user_id: str, permission_level: str) -> None:
        con = self._connect()
        try:
            con.execute(
                "UPDATE user_permissions SET revoked_at=CURRENT_TIMESTAMP "
                "WHERE local_user_id=? AND permission_level=? AND revoked_at IS NULL",
                [local_user_id, permission_level],
            )
        finally:
            con.close()

    def has_permission(self, local_user_id: str, permission_level: str) -> bool:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT 1 FROM user_permissions "
                "WHERE local_user_id=? AND permission_level=? AND revoked_at IS NULL",
                [local_user_id, permission_level],
            ).fetchone()
        finally:
            con.close()
        return row is not None

    def list_users(self) -> list[str]:
        con = self._connect()
        try:
            rows = con.execute("SELECT local_user_id FROM user_profiles").fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]
