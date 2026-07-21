from __future__ import annotations

from database.db import get_connection
from data_hub.services import FinanceNewsService


def main() -> None:
    service = FinanceNewsService()

    result = service.get_finance_news(
        symbol="600172.SH",
        limit=20,
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

    print("\n统一新闻结果：")
    print(f"股票代码：{result.symbol}")
    print(f"查询词：{result.query}")
    print(f"新闻数量：{len(result.records)}")
    print(f"未核验数量：{result.unverified_count}")

    if not result.records:
        raise RuntimeError("新闻服务没有返回数据")

    latest = result.records[0]

    print("\n最新新闻：")
    print(f"发布时间：{latest.event_time.isoformat()}")
    print(f"新闻标题：{latest.data.get('title')}")
    print(f"文章来源：{latest.data.get('publisher')}")
    print(f"新闻链接：{latest.source_url}")
    print(f"来源等级：{latest.source_level.value}")
    print(f"是否核验：{latest.verified}")

    if not latest.data.get("title"):
        raise RuntimeError("最新新闻标题为空")

    if not latest.data.get("publisher"):
        raise RuntimeError("最新新闻文章来源为空")

    if not latest.source_url:
        raise RuntimeError("最新新闻链接为空")

    if latest.verified:
        raise RuntimeError("媒体新闻不应被直接标记为已核验")

    if latest.source_level.value != "media":
        raise RuntimeError("新闻来源等级不是 media")

    if result.unverified_count != len(result.records):
        raise RuntimeError("新闻核验状态统计不一致")

    with get_connection() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_records
            WHERE symbol = '600172.SH'
              AND data_type = 'finance_news'
            """
        ).fetchone()[0]

    print(f"\nDuckDB 新闻记录数：{count}")
    print("\n统一 get_finance_news 服务检查通过")


if __name__ == "__main__":
    main()