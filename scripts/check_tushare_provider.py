from __future__ import annotations

from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.providers import TushareProvider


def main() -> None:
    provider = TushareProvider()

    records = provider.get_daily_bars(
        symbol="600172.SH",
        start_date="20260701",
        end_date="20260715",
    )

    if not records:
        raise RuntimeError("Tushare Provider 未返回任何日线记录")

    latest = records[-1]

    print("Tushare Provider 转换成功")
    print(f"返回记录数：{len(records)}")
    print(f"股票代码：{latest.symbol}")
    print(f"交易时间：{latest.event_time.isoformat()}")
    print(f"收盘价：{latest.data['close']}")
    print(f"来源：{latest.source_name}")
    print(f"已核验：{latest.verified}")
    print(f"内容哈希：{latest.content_hash[:16]}...")

    initialize_database()

    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT record_id
            FROM data_records
            WHERE content_hash = ?
            LIMIT 1
            """,
            [latest.content_hash],
        ).fetchone()

        if existing is None:
            insert_market_record(connection, latest)
            print("最新记录已写入 DuckDB")
        else:
            print("数据库中已存在相同记录，本次跳过重复写入")

        stored = connection.execute(
            """
            SELECT
                symbol,
                data_type,
                event_time,
                source_name,
                verified,
                payload_json
            FROM data_records
            WHERE content_hash = ?
            LIMIT 1
            """,
            [latest.content_hash],
        ).fetchone()

        if stored is None:
            raise RuntimeError("未能从 DuckDB 读取刚才的记录")

        print("\n数据库记录检查：")
        print(f"股票代码：{stored[0]}")
        print(f"数据类型：{stored[1]}")
        print(f"事件时间：{stored[2]}")
        print(f"数据来源：{stored[3]}")
        print(f"已核验：{stored[4]}")

        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_records
            WHERE source_name = 'Tushare Pro'
              AND symbol = '600172.SH'
              AND data_type = 'daily_bar'
            """
        ).fetchone()[0]

        print(f"Tushare 日线数据库记录数：{count}")

    print("\nTushare 主数据源适配器检查通过")


if __name__ == "__main__":
    main()