"""迁移：decision_packets_v2 表新增冻结区间字段

执行方式：
  python -m trading.decision_support.migration_add_frozen_zones

功能：
  1. 检测 decision_packets_v2 表是否存在
  2. 检测各冻结字段是否已存在（幂等）
  3. 仅对不存在的字段执行 ALTER TABLE ADD COLUMN
  4. 不修改、不删除任何原有字段和数据
"""
from __future__ import annotations

import sys
from pathlib import Path


COLUMNS_TO_ADD = [
    ("reference_price", "DOUBLE DEFAULT 0"),
    ("frozen_entry_zone_json", "VARCHAR"),
    ("frozen_preferred_zone_json", "VARCHAR"),
    ("frozen_stop_loss_price", "DOUBLE DEFAULT 0"),
    ("zone_generated_at", "VARCHAR"),
    ("zone_version", "VARCHAR DEFAULT 'V1'"),
    ("zone_valid_until", "VARCHAR"),
]


def get_existing_columns(con, table_name: str) -> set[str]:
    """获取表的已有列名"""
    rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def migrate(db_path: str | Path) -> dict:
    """执行迁移，返回迁移结果"""
    import duckdb

    db_path = Path(db_path)
    if not db_path.exists():
        return {"status": "skip", "reason": f"数据库文件不存在: {db_path}"}

    con = duckdb.connect(str(db_path))
    try:
        # 检查表是否存在
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name='decision_packets_v2'"
        ).fetchall()
        if not tables:
            return {"status": "skip", "reason": "decision_packets_v2 表不存在"}

        existing = get_existing_columns(con, "decision_packets_v2")
        added = []
        skipped = []

        for col_name, col_type in COLUMNS_TO_ADD:
            if col_name in existing:
                skipped.append(col_name)
            else:
                con.execute(f"ALTER TABLE decision_packets_v2 ADD COLUMN {col_name} {col_type}")
                added.append(col_name)

        return {
            "status": "ok",
            "added": added,
            "skipped": skipped,
            "total_columns": len(existing) + len(added),
        }
    finally:
        con.close()


if __name__ == "__main__":
    from config.settings import settings

    db_path = settings.opc_database_path
    if not Path(db_path).is_absolute():
        db_path = Path(__file__).resolve().parents[2] / db_path

    print(f"迁移数据库: {db_path}")
    result = migrate(db_path)
    print(f"结果: {result}")
    if result["status"] == "ok" and result["added"]:
        print(f"新增字段: {result['added']}")
    elif result["status"] == "ok":
        print("所有字段已存在，无需迁移")
    else:
        print(f"跳过: {result.get('reason', '未知原因')}")
