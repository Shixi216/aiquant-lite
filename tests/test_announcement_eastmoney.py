from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from data_hub.services.announcement_eastmoney import EastmoneyAnnouncement
from data_hub.services.announcement_service import AnnouncementService


TZ = ZoneInfo("Asia/Shanghai")


class _EastmoneyOK:
    def fetch(self, symbol: str, start_date: str, end_date: str):
        return [
            EastmoneyAnnouncement(
                symbol="688636",
                title="智明达:成都智明达电子股份有限公司重要事项提示性公告",
                disclosure_time=datetime(2026, 7, 20, 20, 38, tzinfo=TZ),
                raw_disclosure_time="2026-07-20 20:38:00:000",
                url="https://data.eastmoney.com/notices/detail/688636/AN1.html",
                art_code="AN1",
                content="从即日起三年，公司在特种领域获取订单受限。",
            )
        ]


class _EastmoneyTimeout:
    def fetch(self, symbol: str, start_date: str, end_date: str):
        raise TimeoutError("mock timeout")


def _cninfo_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "688636",
                "简称": "智明达",
                "公告标题": "成都智明达电子股份有限公司重要事项提示性公告",
                "公告时间": "2026-07-21 00:00:00",
                "公告链接": "https://static.cninfo.com.cn/finalpage/2026-07-21/1.pdf",
            }
        ]
    )


def test_dual_source_uses_earlier_eastmoney_time(monkeypatch) -> None:
    service = AnnouncementService(eastmoney_service=_EastmoneyOK())
    monkeypatch.setattr(service, "_fetch", lambda **_: _cninfo_frame())
    response = service.get_announcements("688636", "20260719", "20260722", persist=False)
    record = response.records[0]
    assert record.event_time == datetime(2026, 7, 20, 20, 38, tzinfo=TZ)
    assert record.data["announcement_date"] == "20260720"
    assert record.data["announcement_time_raw"].startswith("2026-07-21")
    assert "Eastmoney" in record.data["disclosure_source"]
    assert "获取订单受限" in record.data["content"]


def test_eastmoney_timeout_falls_back_to_cninfo(monkeypatch) -> None:
    service = AnnouncementService(eastmoney_service=_EastmoneyTimeout())
    monkeypatch.setattr(service, "_fetch", lambda **_: _cninfo_frame())
    response = service.get_announcements("688636", "20260719", "20260722", persist=False)
    record = response.records[0]
    assert record.event_time == datetime(2026, 7, 21, 0, 0, tzinfo=TZ)
    assert record.data["announcement_date"] == "20260721"
    assert record.data["disclosure_source"] == "CNInfo"
    eastmoney_run = next(run for run in response.provider_runs if "Eastmoney" in run.provider)
    assert eastmoney_run.success is False
