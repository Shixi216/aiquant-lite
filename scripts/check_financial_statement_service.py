from __future__ import annotations

from database.db import get_connection
from data_hub.services import FinancialStatementService


def main() -> None:
    service = FinancialStatementService()

    result = service.get_financial_statement(
        symbol="600172.SH",
        start_date="20240101",
        end_date="20260716",
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

    print("\n统一财务报表结果：")
    print(f"股票代码：{result.symbol}")
    print(f"共同报告期：{result.report_period}")
    print(f"报表完整：{result.complete}")
    print(f"报表数量：{len(result.records)}")

    records = {
        str(record.data["statement_type"]): record
        for record in result.records
    }

    income = records.get("income")
    balance = records.get("balancesheet")
    cashflow = records.get("cashflow")

    if income:
        print("\n利润表：")
        print(
            "营业收入："
            f"{income.data.get('total_revenue')}"
        )
        print(
            "归母净利润："
            f"{income.data.get('n_income_attr_p')}"
        )

    if balance:
        print("\n资产负债表：")
        print(
            "总资产："
            f"{balance.data.get('total_assets')}"
        )
        print(
            "总负债："
            f"{balance.data.get('total_liab')}"
        )

    if cashflow:
        print("\n现金流量表：")
        print(
            "经营现金流："
            f"{cashflow.data.get('n_cashflow_act')}"
        )
        print(
            "期末现金："
            f"{cashflow.data.get('c_cash_equ_end_period')}"
        )

    if not result.complete:
        raise RuntimeError(
            f"财务报表不完整：{result.missing_statements}"
        )

    if len(result.records) != 3:
        raise RuntimeError("财务报表数量不是3")

    report_periods = {
        str(record.data.get("report_period"))
        for record in result.records
    }

    if len(report_periods) != 1:
        raise RuntimeError("三张报表的报告期不一致")

    with get_connection() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_records
            WHERE symbol = '600172.SH'
              AND data_type = 'financial_statement'
            """
        ).fetchone()[0]

    print(f"\nDuckDB 财务报表记录数：{count}")
    print("\n统一 get_financial_statement 服务检查通过")


if __name__ == "__main__":
    main()