"""三用户绑定初始化（阶段4.4）

创建默认用户 + 管理员，建立企业微信绑定映射。
按任务书：企业微信最多三名用户。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    from trading.decision_support.user_repository import UserRepository
    from trading.decision_support.permission_service import PermissionService

    repo = UserRepository()
    svc = PermissionService(repo)

    print("=" * 50)
    print("三用户绑定初始化")
    print("=" * 50)

    # 1. 管理员（现有企微用户 ZhangTianYi）
    admin_id = "u_wecom_ZhangTianYi"
    if repo.get_user(admin_id) is None:
        repo.create_user(admin_id, "管理员", "HIGH", is_admin=True)
        print(f"✅ 创建管理员: {admin_id}")
    repo.bind_external_user("wecom", "ZhangTianYi", admin_id)
    print(f"✅ 绑定企微: ZhangTianYi → {admin_id}")

    # 2. 三个普通用户位（待绑定，先创建空档案）
    for i in (1, 2, 3):
        uid = f"user_{i}"
        if repo.get_user(uid) is None:
            repo.create_user(uid, f"用户{i}", "MEDIUM")
            # 默认授予 LEVEL3（管理员授权），LEVEL1/2默认有
            repo.grant_permission(uid, "LEVEL_3_LOCAL_WRITE", admin_id)
            print(f"✅ 创建用户{i}: {uid}（已授权LEVEL3）")

    # 3. 输出当前用户清单
    print("\n当前用户清单:")
    for uid in repo.list_users():
        u = repo.get_user(uid)
        flag = "管理员" if u.is_admin else "普通用户"
        print(f"  {uid} | {u.display_name} | {flag} | 风险{u.risk_level}")

    # 4. 绑定关系表
    print("\n绑定关系:")
    print("  wecom ZhangTianYi → u_wecom_ZhangTianYi（管理员）")
    print("  wecom <待绑定> → user_1 / user_2 / user_3")
    print("\n提示: 新用户首次发言后，用 bind_external_user 绑定实际企微ID")


if __name__ == "__main__":
    main()
