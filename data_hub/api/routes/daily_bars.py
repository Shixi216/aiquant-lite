from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from data_hub.schemas.service import DailyBarsResponse
from data_hub.services import DailyBarsService


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
    "/{symbol}/daily-bars",
    response_model=DailyBarsResponse,
    summary="获取并核验A股日线行情",
)
def get_daily_bars(
    symbol: str,
    start_date: str = Query(
        ...,
        description="开始日期，格式 YYYYMMDD",
    ),
    end_date: str = Query(
        ...,
        description="结束日期，格式 YYYYMMDD",
    ),
    persist: bool = Query(
        default=True,
        description="是否将所有来源记录写入 DuckDB",
    ),
) -> DailyBarsResponse:
    start = _validate_date(start_date, "start_date")
    end = _validate_date(end_date, "end_date")

    if start > end:
        raise HTTPException(
            status_code=422,
            detail="start_date 不能晚于 end_date",
        )

    try:
        return DailyBarsService().get_daily_bars(
            symbol=symbol,
            start_date=start,
            end_date=end,
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
                "日线行情服务发生未处理异常："
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc