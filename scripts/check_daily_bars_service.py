from __future__ import annotations

from database.db import get_connection
from data_hub.services import DailyBarsService


def main() -> None:
    service = DailyBarsService()

    result = service.get_daily_bars(
        symbol="600172.SH",
        start_date="20260701",
        end_date="20260715",
        persist=True,
    )

    print("数据源执行情况：")

    for run in result.provider_runs:
        status = "成功" if run.success else "失败"

        print(
            f"- {run.provider}: {status}, "
            f"records={run.record_count}, "
            f"latency={run.latency_ms}ms"
        )

        if not run.success:
            print(
                f"  {run.error_type}: "
                f"{run.error_message}"
            )

    if not result.records:
        raise RuntimeError("统一服务没有返回日线记录")

    latest = result.records[-1]

    print("\n统一服务结果：")
    print(f"股票代码：{result.symbol}")
    print(f"主数据源：{result.primary_source}")
    print(f"日线记录数：{len(result.records)}")
    print(f"最新交易日：{latest.data['trade_date']}")
    print(f"最新收盘价：{latest.data['close']}")
    print(f"最新记录已核验：{latest.verified}")
    print(f"已核验日期数：{len(result.verified_dates)}")
    print(f"未核验日期数：{len(result.unverified_dates)}")

    successful = [
        run
        for run in result.provider_runs
        if run.success
    ]

    if len(successful) < 2:
        raise RuntimeError("成功数据源不足两个，无法形成交叉核验")

    if latest.data["trade_date"] != "20260715":
        raise RuntimeError("最新交易日与预期不一致")

    if float(latest.data["close"]) != 14.07:
        raise RuntimeError("最新收盘价与预期不一致")

    if not latest.verified:
        raise RuntimeError("最新日线没有通过交叉核验")

    with get_connection() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_records
            WHERE symbol = '600172.SH'
              AND data_type = 'daily_bar'
            """
        ).fetchone()[0]

    print(f"DuckDB 日线总记录数：{count}")
    print("\n统一 get_daily_bars 服务检查通过")


if __name__ == "__main__":
    main()