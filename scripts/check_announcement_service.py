from __future__ import annotations

from urllib.parse import urlparse

from database.db import get_connection
from data_hub.services import AnnouncementService


def main() -> None:
    service = AnnouncementService()

    result = service.get_announcements(
        symbol="600172.SH",
        start_date="20260101",
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

    print("\n统一公告结果：")
    print(f"股票代码：{result.symbol}")
    print(f"公告数量：{len(result.records)}")
    print(f"已核验数量：{result.verified_count}")

    if not result.records:
        raise RuntimeError("公告服务没有返回数据")

    latest = result.records[0]

    print("\n最新公告：")
    print(
        "公告日期："
        f"{latest.data.get('announcement_date')}"
    )
    print(f"公告标题：{latest.data.get('title')}")
    print(f"公告链接：{latest.source_url}")
    print(f"官方来源：{latest.verified}")

    if not latest.data.get("title"):
        raise RuntimeError("最新公告标题为空")

    if not latest.source_url:
        raise RuntimeError("最新公告原文链接为空")

    host = (
        urlparse(str(latest.source_url)).hostname or ""
    ).lower()

    if not (
        host == "cninfo.com.cn"
        or host.endswith(".cninfo.com.cn")
    ):
        raise RuntimeError(
            f"最新公告不是巨潮资讯官方链接：{host}"
        )

    if not latest.verified:
        raise RuntimeError("官方公告没有标记为已核验")

    if result.verified_count != len(result.records):
        raise RuntimeError("存在未通过官方链接核验的公告")

    with get_connection() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_records
            WHERE symbol = '600172.SH'
              AND data_type = 'announcement'
            """
        ).fetchone()[0]

    print(f"\nDuckDB 公告记录数：{count}")
    print("\n统一 get_announcements 服务检查通过")


if __name__ == "__main__":
    main()