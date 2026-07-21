from __future__ import annotations

from database.db import get_connection
from data_hub.services import RealtimeQuoteService


def main() -> None:
    service = RealtimeQuoteService()
    result = service.get_realtime_quote("600172.SH")

    print("数据源执行情况：")

    for run in result.provider_runs:
        status = "成功" if run.success else "失败"

        print(
            f"- {run.provider}: {status}, "
            f"latency={run.latency_ms}ms"
        )

        if not run.success:
            print(
                f"  {run.error_type}: "
                f"{run.error_message}"
            )

    print("\n统一报价结果：")
    print(f"股票代码：{result.symbol}")
    print(f"价格：{result.record.data.get('price')}")
    print(f"数据时间：{result.record.event_time.isoformat()}")
    print(f"数据来源：{result.record.source_name}")
    print(f"是否实时：{result.is_realtime}")
    print(f"是否降级：{result.fallback_used}")
    print(f"是否核验：{result.record.verified}")

    if result.warning:
        print(f"警告：{result.warning}")

    price = float(result.record.data.get("price") or 0)

    if price <= 0:
        raise RuntimeError("统一报价没有返回有效价格")

    if result.fallback_used and not result.record.verified:
        raise RuntimeError("降级报价没有通过多源核验")

    with get_connection() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_records
            WHERE symbol = '600172.SH'
              AND data_type = 'realtime_quote'
            """
        ).fetchone()[0]

    print(f"DuckDB 报价记录数：{count}")
    print("\n统一 get_realtime_quote 服务检查通过")


if __name__ == "__main__":
    main()