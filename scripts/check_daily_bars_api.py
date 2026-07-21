from __future__ import annotations

from fastapi.testclient import TestClient

from data_hub.api import app


def main() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/stocks/600172.SH/daily-bars",
            params={
                "start_date": "20260701",
                "end_date": "20260715",
                "persist": "false",
            },
        )

        print("日线行情接口：")
        print(f"状态码：{response.status_code}")

        data = response.json()

        if response.status_code != 200:
            raise RuntimeError(f"接口请求失败：{data}")

        records = data.get("records", [])
        provider_runs = data.get("provider_runs", [])

        print(f"股票代码：{data.get('symbol')}")
        print(f"主数据源：{data.get('primary_source')}")
        print(f"日线数量：{len(records)}")
        print(
            "已核验日期数："
            f"{len(data.get('verified_dates', []))}"
        )
        print(
            "未核验日期数："
            f"{len(data.get('unverified_dates', []))}"
        )

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

        if not records:
            raise RuntimeError("HTTP 接口没有返回日线")

        latest = records[-1]
        latest_data = latest.get("data", {})

        print("\n最新日线：")
        print(f"交易日期：{latest_data.get('trade_date')}")
        print(f"收盘价：{latest_data.get('close')}")
        print(f"核验状态：{latest.get('verified')}")

        if data.get("symbol") != "600172.SH":
            raise RuntimeError("股票代码错误")

        if latest_data.get("trade_date") != "20260715":
            raise RuntimeError("最新交易日错误")

        if float(latest_data.get("close") or 0) != 14.07:
            raise RuntimeError("最新收盘价错误")

        if not latest.get("verified"):
            raise RuntimeError("最新日线未通过核验")

        successful_count = sum(
            1
            for run in provider_runs
            if run.get("success")
        )

        if successful_count < 2:
            raise RuntimeError(
                "成功数据源不足两个，无法交叉核验"
            )

        invalid_response = client.get(
            "/v1/stocks/600172.SH/daily-bars",
            params={
                "start_date": "20260720",
                "end_date": "20260701",
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

    print("\nget_daily_bars HTTP 接口检查通过")


if __name__ == "__main__":
    main()