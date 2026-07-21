from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from data_hub.schemas.service import AnnouncementResponse
from data_hub.services import AnnouncementService


router = APIRouter(
    prefix="/v1/stocks",
    tags=["stocks"],
)


def _validate_date(value: str, field_name: str) -> str:
    normalized = value.replace("-", "").strip()

    if len(normalized) != 8 or not normalized.isdigit():
        raise HTTPException(
            status_code=422,
            detail=(
                f"{field_name} 必须是 "
                "YYYYMMDD 或 YYYY-MM-DD"
            ),
        )

    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} 不是有效日期：{value}",
        ) from exc

    return normalized


@router.get(
    "/{symbol}/announcements",
    response_model=AnnouncementResponse,
    summary="获取巨潮资讯上市公司公告",
)
def get_announcements(
    symbol: str,
    start_date: str = Query(
        ...,
        description="开始日期，格式 YYYYMMDD",
    ),
    end_date: str = Query(
        ...,
        description="结束日期，格式 YYYYMMDD",
    ),
    keyword: str = Query(
        default="",
        max_length=100,
        description="公告标题关键词",
    ),
    category: str = Query(
        default="",
        max_length=100,
        description="巨潮资讯公告类别",
    ),
    persist: bool = Query(
        default=True,
        description="是否将公告写入本地 DuckDB",
    ),
) -> AnnouncementResponse:
    start = _validate_date(start_date, "start_date")
    end = _validate_date(end_date, "end_date")

    if start > end:
        raise HTTPException(
            status_code=422,
            detail="start_date 不能晚于 end_date",
        )

    try:
        return AnnouncementService().get_announcements(
            symbol=symbol,
            start_date=start,
            end_date=end,
            keyword=keyword.strip(),
            category=category.strip(),
            persist=persist,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "公告服务发生未处理异常："
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc

@router.get(
    "/{symbol}/announcements/resolve",
    response_model=None,
    summary="从本地数据库精确解析官方公告",
)
def resolve_persisted_announcement(
    symbol: str,
    record_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=64,
    ),
    announcement_id: str | None = Query(
        default=None,
        min_length=6,
        max_length=30,
    ),
) -> dict[str, object]:
    from data_hub.services.announcement_lookup_service import (
        AnnouncementLookupService,
    )

    try:
        record = AnnouncementLookupService().resolve(
            symbol=symbol,
            record_id=record_id,
            announcement_id=announcement_id,
        )

        return record.model_dump(mode="json")

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc