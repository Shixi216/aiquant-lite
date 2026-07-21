from __future__ import annotations

from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.providers import (
    AKShareProvider,
    BaoStockProvider,
    TushareProvider,
)
from data_hub.verification import verify_daily_bars


SYMBOL = "600172.SH"
START_DATE = "20260701"
END_DATE = "20260715"


def insert_if_absent(connection, record) -> None:
    existing = connection.execute(
        """
        SELECT record_id
        FROM data_records
        WHERE content_hash = ?
        LIMIT 1
        """,
        [record.content_hash],
    ).fetchone()

    if existing is None:
        insert_market_record(connection, record)


def main() -> None:
    print("正在读取 Tushare……", flush=True)
    tushare_records = TushareProvider().get_daily_bars(
        SYMBOL,
        START_DATE,
        END_DATE,
    )

    print("正在读取 AKShare……", flush=True)
    akshare_records = AKShareProvider().get_daily_bars(
        SYMBOL,
        START_DATE,
        END_DATE,
    )

    print("正在读取 BaoStock……", flush=True)
    baostock_records = BaoStockProvider().get_daily_bars(
        SYMBOL,
        START_DATE,
        END_DATE,
    )

    if not tushare_records:
        raise RuntimeError("Tushare 未返回日线")
    if not akshare_records:
        raise RuntimeError("AKShare 未返回日线")
    if not baostock_records:
        raise RuntimeError("BaoStock 未返回日线")

    latest_records = [
        tushare_records[-1],
        akshare_records[-1],
        baostock_records[-1],
    ]

    print("\n三源最新记录：")
    for record in latest_records:
        print(
            f"- {record.source_name}: "
            f"{record.data['trade_date']} "
            f"close={record.data['close']}"
        )

    result = verify_daily_bars(latest_records)

    print("\n核验结果：")
    print(f"交易日期：{result.trade_date}")
    print(f"参与来源：{', '.join(result.sources)}")
    print(f"匹配字段：{', '.join(result.matched_fields)}")
    print(f"核验通过：{result.verified}")

    if result.mismatches:
        print(f"不一致字段：{result.mismatches}")

    initialize_database()

    with get_connection() as connection:
        for record in latest_records:
            insert_if_absent(connection, record)

        hashes = [record.content_hash for record in latest_records]

        if result.verified:
            connection.execute(
                """
                UPDATE data_records
                SET verified = TRUE
                WHERE content_hash IN (?, ?, ?)
                """,
                hashes,
            )

        stored = connection.execute(
            """
            SELECT
                source_name,
                symbol,
                event_time,
                verified,
                payload_json
            FROM data_records
            WHERE content_hash IN (?, ?, ?)
            ORDER BY source_name
            """,
            hashes,
        ).fetchall()

        print("\n数据库中的三源记录：")
        for row in stored:
            print(
                f"- {row[0]} | {row[1]} | "
                f"{row[2]} | verified={row[3]}"
            )

    if not result.verified:
        raise RuntimeError("三源价格核验未通过")

    print("\n三源行情交叉核验通过")


if __name__ == "__main__":
    main()