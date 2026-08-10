from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import baostock as bs
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from config.network import configure_network_policy


def _query_to_frame(query: Any) -> pd.DataFrame:
    if query.error_code != "0":
        raise RuntimeError(f"BaoStock query failed: {query.error_code} {query.error_msg}")
    rows: list[list[str]] = []
    while query.error_code == "0" and query.next():
        rows.append(query.get_row_data())
    return pd.DataFrame(rows, columns=query.fields)


@dataclass(frozen=True)
class ProviderBatchResult:
    provider: str
    capability: str
    frame: pd.DataFrame
    request_count: int
    metadata: dict[str, Any]


class AKShareBatchProvider:
    provider_name = "AKShare"

    def __init__(self) -> None:
        configure_network_policy()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    def fetch_stock_list(self) -> ProviderBatchResult:
        import akshare as ak  # 延迟加载
        frame = ak.stock_info_a_code_name()
        return ProviderBatchResult(
            provider=self.provider_name,
            capability="FULL_STOCK_LIST",
            frame=frame,
            request_count=1,
            metadata={"endpoint": "stock_info_a_code_name"},
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    def fetch_market_snapshot(self) -> ProviderBatchResult:
        import akshare as ak  # 延迟加载
        frame = ak.stock_zh_a_spot_em()
        return ProviderBatchResult(
            provider=self.provider_name,
            capability="FULL_MARKET_REALTIME",
            frame=frame,
            request_count=1,
            metadata={"endpoint": "stock_zh_a_spot_em"},
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    def fetch_announcements(self, trade_date: date) -> ProviderBatchResult:
        import akshare as ak  # 延迟加载
        frame = ak.stock_notice_report(date=trade_date.strftime("%Y%m%d"))
        return ProviderBatchResult(
            provider=self.provider_name,
            capability="BATCH_ANNOUNCEMENTS_BY_DATE",
            frame=frame,
            request_count=1,
            metadata={
                "endpoint": "stock_notice_report",
                "trade_date": trade_date.isoformat(),
            },
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    def fetch_global_finance_news(self) -> ProviderBatchResult:
        import akshare as ak  # 延迟加载
        frame = ak.stock_info_global_em()
        return ProviderBatchResult(
            provider=self.provider_name,
            capability="BATCH_FINANCE_NEWS_LATEST",
            frame=frame,
            request_count=1,
            metadata={
                "endpoint": "stock_info_global_em",
                "historical_window_supported": False,
            },
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    def fetch_margin_sse(self, trade_date: date) -> ProviderBatchResult:
        import akshare as ak  # 延迟加载
        frame = ak.stock_margin_detail_sse(date=trade_date.strftime("%Y%m%d"))
        return ProviderBatchResult(
            provider=self.provider_name,
            capability="MARGIN_BY_DATE_SSE",
            frame=frame,
            request_count=1,
            metadata={"endpoint": "stock_margin_detail_sse"},
        )


class BaoStockBatchProvider:
    provider_name = "BaoStock"

    def __init__(self) -> None:
        configure_network_policy()

    @staticmethod
    def _session(function: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(
                f"BaoStock login failed: {login.error_code} {login.error_msg}"
            )
        try:
            return function()
        finally:
            bs.logout()

    def fetch_stock_basic(self) -> ProviderBatchResult:
        frame = self._session(lambda: _query_to_frame(bs.query_stock_basic()))
        return ProviderBatchResult(
            provider=self.provider_name,
            capability="FULL_STOCK_BASIC",
            frame=frame,
            request_count=1,
            metadata={"endpoint": "query_stock_basic"},
        )

    def fetch_stock_list_by_date(self, trade_date: date) -> ProviderBatchResult:
        frame = self._session(
            lambda: _query_to_frame(
                bs.query_all_stock(day=trade_date.isoformat())
            )
        )
        return ProviderBatchResult(
            provider=self.provider_name,
            capability="FULL_STOCK_LIST_BY_DATE",
            frame=frame,
            request_count=1,
            metadata={
                "endpoint": "query_all_stock",
                "trade_date": trade_date.isoformat(),
            },
        )

    def fetch_industry_memberships(self) -> ProviderBatchResult:
        frame = self._session(
            lambda: _query_to_frame(bs.query_stock_industry())
        )
        return ProviderBatchResult(
            provider=self.provider_name,
            capability="FULL_INDUSTRY_MEMBERSHIPS",
            frame=frame,
            request_count=1,
            metadata={"endpoint": "query_stock_industry"},
        )


__all__ = [
    "AKShareBatchProvider",
    "BaoStockBatchProvider",
    "ProviderBatchResult",
]
