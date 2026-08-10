from __future__ import annotations

import hashlib
import json
from datetime import datetime
from time import perf_counter
from typing import Callable
from zoneinfo import ZoneInfo

import baostock as bs

from config.network import configure_network_policy
from config.settings import settings
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.service import ProviderRun, StockBasicResponse


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _normalize_symbol(value: str) -> tuple[str, str]:
    normalized = value.strip().upper()
    code = normalized.split(".", 1)[0]

    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效股票代码：{value}")

    if "." in normalized:
        exchange = normalized.split(".", 1)[1]
    elif code.startswith(("5", "6", "9")):
        exchange = "SH"
    elif code.startswith(("4", "8")):
        exchange = "BJ"
    else:
        exchange = "SZ"

    if exchange not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"不支持的交易所：{exchange}")

    return code, f"{code}.{exchange}"


def _event_time(value: str | None) -> datetime:
    if value:
        normalized = value.replace("-", "").strip()

        if len(normalized) == 8 and normalized.isdigit():
            return datetime.strptime(
                normalized,
                "%Y%m%d",
            ).replace(tzinfo=SHANGHAI_TZ)

    return datetime.now(tz=SHANGHAI_TZ)


def _content_hash(
    source_name: str,
    symbol: str,
    payload: dict[str, object],
) -> str:
    canonical = json.dumps(
        {
            "source": source_name,
            "symbol": symbol,
            "data_type": DataType.STOCK_BASIC.value,
            "data": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_name(value: object) -> str:
    return "".join(str(value or "").split()).upper()


class StockBasicService:
    """Fetch, normalize, verify, and persist A-share basic information."""

    def __init__(self) -> None:
        configure_network_policy()

    def _fetch_tushare(self, symbol: str) -> MarketRecord:
        if not settings.tushare_token:
            raise RuntimeError("TUSHARE_TOKEN 未配置")

        import tushare as ts  # 延迟加载
        client = ts.pro_api(settings.tushare_token.strip())

        frame = client.stock_basic(
            ts_code=symbol,
            fields=(
                "ts_code,symbol,name,area,industry,market,"
                "exchange,list_status,list_date"
            ),
        )

        if frame.empty:
            raise RuntimeError(f"Tushare 未找到股票：{symbol}")

        row = frame.iloc[0]

        payload: dict[str, object] = {
            "symbol": str(row["ts_code"]),
            "code": str(row["symbol"]),
            "name": str(row["name"]),
            "area": str(row.get("area") or ""),
            "industry": str(row.get("industry") or ""),
            "market": str(row.get("market") or ""),
            "exchange": str(row.get("exchange") or ""),
            "list_status": str(row.get("list_status") or ""),
            "list_date": str(row.get("list_date") or ""),
        }

        source_name = "Tushare Pro"

        return MarketRecord(
            symbol=symbol,
            data_type=DataType.STOCK_BASIC,
            event_time=_event_time(payload["list_date"]),
            source_name=source_name,
            source_level=SourceLevel.STRUCTURED,
            verified=False,
            content_hash=_content_hash(source_name, symbol, payload),
            data=payload,
        )

    def _fetch_baostock(
        self,
        code: str,
        symbol: str,
    ) -> MarketRecord:
        exchange = symbol.split(".", 1)[1]

        if exchange == "BJ":
            raise RuntimeError("BaoStock 暂不支持北交所股票基础信息核验")

        baostock_code = f"{exchange.lower()}.{code}"
        login_result = bs.login()

        if login_result.error_code != "0":
            raise RuntimeError(
                "BaoStock 登录失败："
                f"{login_result.error_code} {login_result.error_msg}"
            )

        try:
            query = bs.query_stock_basic(code=baostock_code)

            if query.error_code != "0":
                raise RuntimeError(
                    "BaoStock 查询失败："
                    f"{query.error_code} {query.error_msg}"
                )

            rows: list[dict[str, str]] = []

            while query.error_code == "0" and query.next():
                rows.append(
                    dict(
                        zip(
                            query.fields,
                            query.get_row_data(),
                            strict=False,
                        )
                    )
                )

            if not rows:
                raise RuntimeError(f"BaoStock 未找到股票：{symbol}")

            row = rows[0]

            payload: dict[str, object] = {
                "symbol": symbol,
                "raw_code": row.get("code"),
                "code": code,
                "name": row.get("code_name"),
                "list_date": row.get("ipoDate"),
                "out_date": row.get("outDate"),
                "security_type": row.get("type"),
                "status": row.get("status"),
                "exchange": exchange,
            }

            source_name = "BaoStock"

            return MarketRecord(
                symbol=symbol,
                data_type=DataType.STOCK_BASIC,
                event_time=_event_time(str(payload["list_date"] or "")),
                source_name=source_name,
                source_level=SourceLevel.STRUCTURED,
                verified=False,
                content_hash=_content_hash(source_name, symbol, payload),
                data=payload,
            )

        finally:
            bs.logout()

    @staticmethod
    def _run_provider(
        provider_name: str,
        function: Callable[[], MarketRecord],
    ) -> tuple[MarketRecord | None, ProviderRun]:
        started = perf_counter()

        try:
            record = function()
            latency_ms = round((perf_counter() - started) * 1000)

            return record, ProviderRun(
                provider=provider_name,
                success=True,
                record_count=1,
                latency_ms=latency_ms,
            )

        except Exception as exc:
            latency_ms = round((perf_counter() - started) * 1000)

            return None, ProviderRun(
                provider=provider_name,
                success=False,
                record_count=0,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    @staticmethod
    def _persist(records: list[MarketRecord]) -> None:
        initialize_database()

        with get_connection() as connection:
            for record in records:
                existing = connection.execute(
                    """
                    SELECT record_id
                    FROM data_records
                    WHERE content_hash = ?
                    LIMIT 1
                    """,
                    [record.content_hash],
                ).fetchone()

                if existing is None:
                    insert_market_record(connection, record)

            verified_hashes = [
                record.content_hash
                for record in records
                if record.verified and record.content_hash
            ]

            if verified_hashes:
                placeholders = ",".join("?" for _ in verified_hashes)

                connection.execute(
                    f"""
                    UPDATE data_records
                    SET verified = TRUE
                    WHERE content_hash IN ({placeholders})
                    """,
                    verified_hashes,
                )

    def get_stock_basic(
        self,
        symbol: str,
        persist: bool = True,
    ) -> StockBasicResponse:
        code, canonical_symbol = _normalize_symbol(symbol)

        tushare_record, tushare_run = self._run_provider(
            "Tushare Pro",
            lambda: self._fetch_tushare(canonical_symbol),
        )

        baostock_record, baostock_run = self._run_provider(
            "BaoStock",
            lambda: self._fetch_baostock(code, canonical_symbol),
        )

        records = [
            record
            for record in (tushare_record, baostock_record)
            if record is not None
        ]

        if not records:
            raise RuntimeError(
                "所有股票基础信息数据源均失败："
                f"Tushare={tushare_run.error_message}; "
                f"BaoStock={baostock_run.error_message}"
            )

        discrepancies: dict[str, dict[str, str | None]] = {}
        verified = False

        if len(records) >= 2:
            names = {
                record.source_name: str(record.data.get("name") or "")
                for record in records
            }

            normalized_names = {
                _normalized_name(value)
                for value in names.values()
                if value
            }

            symbols = {
                record.source_name: record.symbol
                for record in records
            }

            if len(set(symbols.values())) != 1:
                discrepancies["symbol"] = symbols

            if len(normalized_names) != 1:
                discrepancies["name"] = names

            verified = not discrepancies

        for record in records:
            record.verified = verified

        if persist:
            self._persist(records)

        canonical_record = tushare_record or baostock_record

        if canonical_record is None:
            raise RuntimeError("无法确定股票基础信息主记录")

        return StockBasicResponse(
            symbol=canonical_symbol,
            record=canonical_record,
            provider_runs=[tushare_run, baostock_run],
            verified=verified,
            discrepancies=discrepancies,
        )