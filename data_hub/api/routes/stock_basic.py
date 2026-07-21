from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from data_hub.schemas.service import StockBasicResponse
from data_hub.services import StockBasicService


router = APIRouter(
    prefix="/v1/stocks",
    tags=["stocks"],
)


@router.get(
    "/{symbol}/basic",
    response_model=StockBasicResponse,
    summary="获取A股基础信息",
)
def get_stock_basic(
    symbol: str,
    persist: bool = Query(
        default=True,
        description="是否将结果写入本地 DuckDB",
    ),
) -> StockBasicResponse:
    try:
        return StockBasicService().get_stock_basic(
            symbol=symbol,
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
                "股票基础信息服务发生未处理异常："
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc