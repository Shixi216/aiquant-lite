from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.schemas.market import (
    DataType,
    MarketRecord,
    SourceLevel,
)


def main() -> None:
    initialize_database()

    sample = MarketRecord(
        symbol="600172.SH",
        data_type=DataType.DAILY_BAR,
        event_time=datetime(
            2026,
            7,
            15,
            15,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        source_name="Tushare",
        source_level=SourceLevel.STRUCTURED,
        verified=True,
        data={
            "open": 15.38,
            "high": 15.71,
            "low": 13.95,
            "close": 14.07,
            "trade_date": "20260715",
        },
    )

    with get_connection() as connection:
        insert_market_record(connection, sample)

        result = connection.execute(
            """
            SELECT
                symbol,
                data_type,
                source_name,
                verified,
                payload_json
            FROM data_records
            WHERE record_id = ?
            """,
            [sample.record_id],
        ).fetchone()

        if result is None:
            raise RuntimeError("写入后未能读取测试记录")

        print("测试记录写入和读取成功")
        print(f"股票代码：{result[0]}")
        print(f"数据类型：{result[1]}")
        print(f"数据来源：{result[2]}")
        print(f"已核验：{result[3]}")

        tables = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
            """
        ).fetchall()

        print("\n已创建数据表：")
        for table in tables:
            print(f"- {table[0]}")

        # 测试完成后删除临时记录，避免污染正式数据。
        connection.execute(
            "DELETE FROM data_records WHERE record_id = ?",
            [sample.record_id],
        )

    print("\nDuckDB 数据库检查通过")


if __name__ == "__main__":
    main()