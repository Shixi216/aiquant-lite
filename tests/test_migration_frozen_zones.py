"""迁移测试：升级前旧数据库 → 新增冻结字段

验证：
1. 旧数据库（无冻结字段）能正常迁移
2. 7个新字段确实存在
3. 旧记录能读取
4. 新记录能写入
5. 原数据数量和内容不变
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

import duckdb


# ═══════════════════════════════════════════════
# 辅助：创建旧版数据库（无冻结字段）
# ═══════════════════════════════════════════════
OLD_SCHEMA = """
CREATE TABLE decision_packets_v2 (
    decision_id VARCHAR PRIMARY KEY,
    local_user_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    position_advice_json VARCHAR,
    trade_plan_id VARCHAR,
    snapshot_id VARCHAR,
    data_cutoff VARCHAR,
    strategy_version VARCHAR,
    factor_versions_json VARCHAR,
    formal_score DOUBLE,
    enhanced_score DOUBLE,
    confidence DOUBLE,
    veto_result_json VARCHAR,
    evidence_ids_json VARCHAR,
    model_call_ids_json VARCHAR,
    created_at VARCHAR,
    expires_at VARCHAR,
    parent_decision_id VARCHAR,
    content_sha256 VARCHAR NOT NULL,
    status VARCHAR DEFAULT 'SAVED'
)
"""


def create_old_database(db_path: str, records: list[dict]) -> None:
    """创建旧版数据库并插入测试数据"""
    con = duckdb.connect(db_path)
    con.execute(OLD_SCHEMA)
    for rec in records:
        con.execute(
            """INSERT INTO decision_packets_v2
            (decision_id, local_user_id, symbol, action, position_advice_json,
             trade_plan_id, snapshot_id, data_cutoff, strategy_version,
             factor_versions_json, formal_score, enhanced_score, confidence,
             veto_result_json, evidence_ids_json, model_call_ids_json,
             created_at, expires_at, parent_decision_id, content_sha256, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                rec["decision_id"], rec["local_user_id"], rec["symbol"], rec["action"],
                json.dumps(rec.get("position_advice", {}), ensure_ascii=False),
                rec.get("trade_plan_id", ""), rec.get("snapshot_id", ""),
                rec.get("data_cutoff", ""), rec.get("strategy_version", "formal-60-40-v1"),
                json.dumps(rec.get("factor_versions", {}), ensure_ascii=False),
                rec.get("formal_score", 0), rec.get("enhanced_score"),
                rec.get("confidence", 0),
                json.dumps(rec.get("veto_result", {}), ensure_ascii=False),
                json.dumps(rec.get("evidence_ids", []), ensure_ascii=False),
                json.dumps(rec.get("model_call_ids", []), ensure_ascii=False),
                rec.get("created_at", ""), rec.get("expires_at", ""),
                rec.get("parent_decision_id", ""), rec.get("content_sha256", "abc"),
                rec.get("status", "SAVED"),
            ],
        )
    con.close()


# ═══════════════════════════════════════════════
# 测试数据
# ═══════════════════════════════════════════════
OLD_RECORDS = [
    {
        "decision_id": "dec_old_001",
        "local_user_id": "user1",
        "symbol": "600010.SH",
        "action": "BUY",
        "formal_score": 0.65,
        "confidence": 0.70,
        "created_at": "2026-07-01T10:00:00",
        "position_advice": {"target_ratio": 0.12},
    },
    {
        "decision_id": "dec_old_002",
        "local_user_id": "user1",
        "symbol": "600519.SH",
        "action": "HOLD",
        "formal_score": 0.45,
        "confidence": 0.60,
        "created_at": "2026-07-02T14:00:00",
    },
    {
        "decision_id": "dec_old_003",
        "local_user_id": "user2",
        "symbol": "000001.SZ",
        "action": "WAIT",
        "formal_score": 0.30,
        "confidence": 0.55,
        "created_at": "2026-07-03T09:30:00",
    },
]


# ═══════════════════════════════════════════════
# 迁移测试
# ═══════════════════════════════════════════════
class TestMigration:
    def test_migrate_old_database(self):
        """旧数据库迁移：7个新字段确实存在"""
        from trading.decision_support.migration_add_frozen_zones import migrate

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "old.duckdb")
            create_old_database(db, OLD_RECORDS)

            # 迁移前：无冻结字段
            con = duckdb.connect(db)
            cols_before = {row[1] for row in con.execute("PRAGMA table_info(decision_packets_v2)").fetchall()}
            con.close()
            assert "reference_price" not in cols_before
            assert "frozen_entry_zone_json" not in cols_before

            # 执行迁移
            result = migrate(db)
            assert result["status"] == "ok"
            assert len(result["added"]) == 7

            # 迁移后：7个新字段存在
            con = duckdb.connect(db)
            cols_after = {row[1] for row in con.execute("PRAGMA table_info(decision_packets_v2)").fetchall()}
            con.close()
            for col in ["reference_price", "frozen_entry_zone_json", "frozen_preferred_zone_json",
                        "frozen_stop_loss_price", "zone_generated_at", "zone_version", "zone_valid_until"]:
                assert col in cols_after, f"字段 {col} 不存在"

    def test_old_records_readable(self):
        """旧记录能正常读取"""
        from trading.decision_support.migration_add_frozen_zones import migrate
        from trading.decision_support.decision_packet_v2 import DecisionPacketV2Repository

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "readable.duckdb")
            create_old_database(db, OLD_RECORDS)
            migrate(db)

            repo = DecisionPacketV2Repository(db_path=Path(db))
            packet = repo.get("dec_old_001")
            assert packet is not None
            assert packet.decision_id == "dec_old_001"
            assert packet.symbol == "600010.SH"
            assert packet.action == "BUY"
            assert packet.formal_score == 0.65
            # 冻结字段为默认值
            assert packet.reference_price == 0.0
            assert packet.frozen_entry_zone == []
            assert packet.frozen_stop_loss_price == 0.0
            assert packet.zone_version == "V1"

    def test_new_records_writable(self):
        """新记录能写入（含冻结字段）"""
        from trading.decision_support.migration_add_frozen_zones import migrate
        from trading.decision_support.decision_packet_v2 import DecisionPacketV2Repository, create_decision_packet

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "writable.duckdb")
            create_old_database(db, OLD_RECORDS)
            migrate(db)

            repo = DecisionPacketV2Repository(db_path=Path(db))
            new_packet = create_decision_packet(
                local_user_id="user1",
                symbol="600882.SH",
                action="BUY",
                formal_score=0.656,
                confidence=0.67,
                reference_price=22.18,
                frozen_entry_zone=[21.07, 22.40],
                frozen_preferred_zone=[21.07, 21.60],
                frozen_stop_loss_price=20.50,
                zone_version="V1",
            )
            saved = repo.save(new_packet)
            assert saved.decision_id.startswith("dec_")

            # 读取新记录
            loaded = repo.get(saved.decision_id)
            assert loaded is not None
            assert loaded.reference_price == 22.18
            assert loaded.frozen_entry_zone == [21.07, 22.40]
            assert loaded.frozen_preferred_zone == [21.07, 21.60]
            assert loaded.frozen_stop_loss_price == 20.50

    def test_original_data_unchanged(self):
        """原数据数量和内容不变"""
        from trading.decision_support.migration_add_frozen_zones import migrate
        from trading.decision_support.decision_packet_v2 import DecisionPacketV2Repository

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "unchanged.duckdb")
            create_old_database(db, OLD_RECORDS)
            migrate(db)

            repo = DecisionPacketV2Repository(db_path=Path(db))

            # 数量不变
            user1_packets = repo.list_by_user("user1")
            user2_packets = repo.list_by_user("user2")
            assert len(user1_packets) == 2
            assert len(user2_packets) == 1

            # 内容不变
            p1 = repo.get("dec_old_001")
            assert p1.symbol == "600010.SH" and p1.action == "BUY" and p1.formal_score == 0.65

            p2 = repo.get("dec_old_002")
            assert p2.symbol == "600519.SH" and p2.action == "HOLD"

            p3 = repo.get("dec_old_003")
            assert p3.symbol == "000001.SZ" and p3.action == "WAIT"

    def test_migration_idempotent(self):
        """迁移幂等：重复执行不报错"""
        from trading.decision_support.migration_add_frozen_zones import migrate

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "idempotent.duckdb")
            create_old_database(db, OLD_RECORDS)

            r1 = migrate(db)
            assert r1["status"] == "ok" and len(r1["added"]) == 7

            r2 = migrate(db)
            assert r2["status"] == "ok" and len(r2["added"]) == 0
            assert len(r2["skipped"]) == 7

    def test_no_modification_to_original_columns(self):
        """不修改任何原有字段"""
        from trading.decision_support.migration_add_frozen_zones import migrate

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "no_modify.duckdb")
            create_old_database(db, OLD_RECORDS)

            # 记录迁移前列信息
            con = duckdb.connect(db)
            cols_before = [(row[1], row[2]) for row in con.execute("PRAGMA table_info(decision_packets_v2)").fetchall()]
            con.close()

            migrate(db)

            # 迁移后原有列不变
            con = duckdb.connect(db)
            cols_after = [(row[1], row[2]) for row in con.execute("PRAGMA table_info(decision_packets_v2)").fetchall()]
            con.close()

            before_dict = {name: typ for name, typ in cols_before}
            after_dict = {name: typ for name, typ in cols_after}
            for name, typ in before_dict.items():
                assert after_dict[name] == typ, f"原列 {name} 类型被修改"
