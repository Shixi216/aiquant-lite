from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from data_hub.schemas.service import RealtimeQuoteResponse
from data_hub.services import RealtimeQuoteService


router = APIRouter(
    prefix="/v1/stocks",
    tags=["stocks"],
)


@router.get(
    "/{symbol}/realtime-quote",
    response_model=RealtimeQuoteResponse,
    summary="获取A股实时报价或已核验降级行情",
)
def get_realtime_quote(
    symbol: str,
    persist: bool = Query(
        default=True,
        description="是否将报价结果写入本地 DuckDB",
    ),
) -> RealtimeQuoteResponse:
    try:
        return RealtimeQuoteService().get_realtime_quote(
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
                "实时报价服务发生未处理异常："
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc