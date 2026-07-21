from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from data_hub.schemas.service import FinancialStatementResponse
from data_hub.services import FinancialStatementService


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
    "/{symbol}/financial-statements",
    response_model=FinancialStatementResponse,
    summary="获取同一报告期的三大财务报表",
)
def get_financial_statements(
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
        description="是否将结果写入本地 DuckDB",
    ),
) -> FinancialStatementResponse:
    start = _validate_date(start_date, "start_date")
    end = _validate_date(end_date, "end_date")

    if start > end:
        raise HTTPException(
            status_code=422,
            detail="start_date 不能晚于 end_date",
        )

    try:
        return FinancialStatementService().get_financial_statement(
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
                "财务报表服务发生未处理异常："
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc