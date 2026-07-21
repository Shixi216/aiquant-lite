from __future__ import annotations

from database.db import get_connection
from data_hub.services import StockBasicService


def main() -> None:
    service = StockBasicService()
    result = service.get_stock_basic("600172.SH")

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

    print("\n统一股票基础信息：")
    print(f"股票代码：{result.symbol}")
    print(f"股票名称：{result.record.data.get('name')}")
    print(f"上市日期：{result.record.data.get('list_date')}")
    print(f"数据来源：{result.record.source_name}")
    print(f"核验通过：{result.verified}")

    if result.discrepancies:
        print(f"差异：{result.discrepancies}")

    if result.symbol != "600172.SH":
        raise RuntimeError("股票代码转换错误")

    if not result.record.data.get("name"):
        raise RuntimeError("股票名称为空")

    successful = [
        run
        for run in result.provider_runs
        if run.success
    ]

    if len(successful) >= 2 and not result.verified:
        raise RuntimeError("两个来源均成功，但基础信息核验未通过")

    with get_connection() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_records
            WHERE symbol = '600172.SH'
              AND data_type = 'stock_basic'
            """
        ).fetchone()[0]

    print(f"DuckDB 基础信息记录数：{count}")
    print("\n统一 get_stock_basic 服务检查通过")


if __name__ == "__main__":
    main()