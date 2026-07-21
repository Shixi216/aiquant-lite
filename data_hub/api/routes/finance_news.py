from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from data_hub.schemas.service import FinanceNewsResponse
from data_hub.services import FinanceNewsService


router = APIRouter(
    prefix="/v1/stocks",
    tags=["stocks"],
)


@router.get(
    "/{symbol}/finance-news",
    response_model=FinanceNewsResponse,
    summary="获取A股个股财经新闻",
)
def get_finance_news(
    symbol: str,
    query: str | None = Query(
        default=None,
        max_length=100,
        description="新闻查询关键词，默认使用股票代码",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="最多返回的新闻数量",
    ),
    persist: bool = Query(
        default=True,
        description="是否将新闻写入本地 DuckDB",
    ),
) -> FinanceNewsResponse:
    normalized_query = query.strip() if query else None

    if query is not None and not normalized_query:
        raise HTTPException(
            status_code=422,
            detail="query 不能为空字符串",
        )

    try:
        return FinanceNewsService().get_finance_news(
            symbol=symbol,
            query=normalized_query,
            limit=limit,
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
                "财经新闻服务发生未处理异常："
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc