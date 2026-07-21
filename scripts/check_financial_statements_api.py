from __future__ import annotations

from fastapi.testclient import TestClient

from data_hub.api import app


EXPECTED_STATEMENTS = {
    "income",
    "balancesheet",
    "cashflow",
}


def main() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/stocks/600172.SH/financial-statements",
            params={
                "start_date": "20240101",
                "end_date": "20260716",
                "persist": "false",
            },
        )

        print("财务报表接口：")
        print(f"状态码：{response.status_code}")

        data = response.json()

        if response.status_code != 200:
            raise RuntimeError(f"接口请求失败：{data}")

        records = data.get("records", [])
        provider_runs = data.get("provider_runs", [])

        print(f"股票代码：{data.get('symbol')}")
        print(f"共同报告期：{data.get('report_period')}")
        print(f"报表完整：{data.get('complete')}")
        print(f"报表数量：{len(records)}")

        print("数据源执行情况：")

        for run in provider_runs:
            status = "成功" if run.get("success") else "失败"

            print(
                f"- {run.get('provider')}: {status}, "
                f"records={run.get('record_count')}, "
                f"latency={run.get('latency_ms')}ms"
            )

            if not run.get("success"):
                print(
                    f"  {run.get('error_type')}: "
                    f"{run.get('error_message')}"
                )

        statement_types = {
            str(record.get("data", {}).get("statement_type"))
            for record in records
        }

        print("\n返回报表类型：")

        for statement_type in sorted(statement_types):
            print(f"- {statement_type}")

        if data.get("symbol") != "600172.SH":
            raise RuntimeError("股票代码错误")

        if data.get("report_period") != "20260331":
            raise RuntimeError(
                "共同报告期与预期不一致："
                f"{data.get('report_period')}"
            )

        if not data.get("complete"):
            raise RuntimeError(
                "三大财务报表不完整："
                f"{data.get('missing_statements')}"
            )

        if statement_types != EXPECTED_STATEMENTS:
            raise RuntimeError(
                "返回的报表类型不完整："
                f"{sorted(statement_types)}"
            )

        report_periods = {
            str(record.get("data", {}).get("report_period"))
            for record in records
        }

        if report_periods != {"20260331"}:
            raise RuntimeError(
                "三张报表的报告期不一致："
                f"{sorted(report_periods)}"
            )

        invalid_response = client.get(
            "/v1/stocks/600172.SH/financial-statements",
            params={
                "start_date": "20260720",
                "end_date": "20240101",
                "persist": "false",
            },
        )

        print("\n无效日期检查：")
        print(f"状态码：{invalid_response.status_code}")
        print(f"返回内容：{invalid_response.json()}")

        if invalid_response.status_code != 422:
            raise RuntimeError(
                "错误日期范围没有返回 422"
            )

    print(
        "\nget_financial_statement HTTP 接口检查通过"
    )


if __name__ == "__main__":
    main()