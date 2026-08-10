from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

from tenacity import retry, stop_after_attempt, wait_exponential

from config.network import configure_network_policy
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.service import ProviderRun, RealtimeQuoteResponse
from data_hub.services.daily_bars_service import DailyBarsService


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


def _to_float(value: object) -> float | None:
    if value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result):
        return None

    return result


def _content_hash(
    source_name: str,
    symbol: str,
    event_time: datetime,
    payload: dict[str, object],
) -> str:
    canonical = json.dumps(
        {
            "source": source_name,
            "symbol": symbol,
            "data_type": DataType.REALTIME_QUOTE.value,
            "event_time": event_time.isoformat(),
            "data": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RealtimeQuoteService:
    """Return an intraday quote or a verified latest-close fallback."""

    def __init__(self) -> None:
        configure_network_policy()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    def _fetch_akshare(self, code: str) -> dict[str, object]:
        import akshare as ak  # 延迟加载（避免模块级 import 耗时）
        frame = ak.stock_bid_ask_em(symbol=code)

        if frame.empty:
            raise RuntimeError("AKShare 实时报价返回空数据")

        if not {"item", "value"}.issubset(frame.columns):
            raise RuntimeError(
                f"AKShare 实时报价字段异常：{list(frame.columns)}"
            )

        return {
            str(row["item"]).strip(): row["value"]
            for _, row in frame.iterrows()
        }

    @staticmethod
    def _fetch_tencent(code: str) -> dict[str, object]:
        """腾讯实时行情源（qt.gtimg.cn）。

        东方财富 push2 接口对非浏览器 TLS 指纹/高频请求实施风控，
        腾讯行情接口更稳定，作为实时报价首选源。
        字段分隔符为 '~'，编码 GBK。
        """
        import requests as _requests

        symbol = code
        market = "sh" if code.startswith("6") else "sz"

        response = _requests.get(
            f"https://qt.gtimg.cn/q={market}{code}",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Referer": "https://gu.qq.com/",
                "Accept": "*/*",
            },
            timeout=8,
        )
        response.raise_for_status()
        response.encoding = "gbk"

        text = response.text.strip()
        if "=" not in text:
            raise RuntimeError("腾讯行情返回格式异常")

        payload = text.split("=", 1)[1].strip().strip('"')
        parts = payload.split("~")

        if len(parts) < 40:
            raise RuntimeError(f"腾讯行情字段不足：{len(parts)}")

        def _num(index: int) -> float | None:
            raw = parts[index] if index < len(parts) else ""
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            return value if value else None

        values: dict[str, object] = {
            "最新": _num(3),
            "昨收": _num(4),
            "今开": _num(5),
            "涨跌": _num(31),
            "涨幅": _num(32),
            "最高": _num(33),
            "最低": _num(34),
            "成交量": _num(6),
            "换手": _num(38),
            "量比": _num(49),
            "名称": parts[1] if len(parts) > 1 else None,
            "时间": parts[30] if len(parts) > 30 else None,
        }

        if values["最新"] is None or float(values["最新"]) <= 0:
            raise RuntimeError("腾讯行情没有返回有效最新价")

        return values

    def _create_realtime_record(
        self,
        code: str,
        symbol: str,
    ) -> MarketRecord:
        # 腾讯源优先（东财 push2 对非浏览器指纹有风控）
        try:
            values = self._fetch_tencent(code)
            source_name = "Tencent Realtime (qt.gtimg.cn)"
        except Exception:
            values = self._fetch_akshare(code)
            source_name = "AKShare / Eastmoney Realtime"

        latest = _to_float(values.get("最新"))

        if latest is None or latest <= 0:
            raise RuntimeError("AKShare 没有返回有效最新价")

        event_time = datetime.now(tz=SHANGHAI_TZ)

        payload: dict[str, object] = {
            "symbol": symbol,
            "quote_type": "realtime",
            "price": latest,
            "open": _to_float(values.get("今开")),
            "high": _to_float(values.get("最高")),
            "low": _to_float(values.get("最低")),
            "previous_close": _to_float(values.get("昨收")),
            "average_price": _to_float(values.get("均价")),
            "change": _to_float(values.get("涨跌")),
            "pct_chg": _to_float(values.get("涨幅")),
            "volume_lot": _to_float(values.get("总手")),
            "amount": _to_float(values.get("金额")),
            "turnover_rate": _to_float(values.get("换手")),
            "volume_ratio": _to_float(values.get("量比")),
            "buy_1": _to_float(values.get("buy_1")),
            "buy_1_volume": _to_float(values.get("buy_1_vol")),
            "sell_1": _to_float(values.get("sell_1")),
            "sell_1_volume": _to_float(values.get("sell_1_vol")),
            "fetched_at": event_time.isoformat(),
        }

        return MarketRecord(
            symbol=symbol,
            data_type=DataType.REALTIME_QUOTE,
            event_time=event_time,
            source_name=source_name,
            source_level=SourceLevel.PUBLIC_WEB,
            verified=False,
            content_hash=_content_hash(
                source_name,
                symbol,
                event_time,
                payload,
            ),
            data=payload,
        )

    @staticmethod
    def _create_fallback_record(symbol: str) -> MarketRecord:
        now = datetime.now(tz=SHANGHAI_TZ)
        start_date = (now.date() - timedelta(days=30)).strftime("%Y%m%d")
        end_date = now.date().strftime("%Y%m%d")

        result = DailyBarsService().get_daily_bars(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            persist=True,
        )

        if not result.records:
            raise RuntimeError("最新日线降级服务没有返回数据")

        latest = result.records[-1]
        price = _to_float(latest.data.get("close"))

        if price is None or price <= 0:
            raise RuntimeError("最新日线记录没有有效收盘价")

        payload: dict[str, object] = {
            "symbol": symbol,
            "quote_type": "latest_close_fallback",
            "price": price,
            "open": _to_float(latest.data.get("open")),
            "high": _to_float(latest.data.get("high")),
            "low": _to_float(latest.data.get("low")),
            "close": price,
            "trade_date": str(latest.data.get("trade_date")),
            "original_source": latest.source_name,
            "verified_source_count": sum(
                1 for run in result.provider_runs if run.success
            ),
        }

        source_name = f"{latest.source_name} / Verified Daily Fallback"

        return MarketRecord(
            symbol=symbol,
            data_type=DataType.REALTIME_QUOTE,
            event_time=latest.event_time,
            source_name=source_name,
            source_level=SourceLevel.STRUCTURED,
            verified=latest.verified,
            content_hash=_content_hash(
                source_name,
                symbol,
                latest.event_time,
                payload,
            ),
            data=payload,
        )

    @staticmethod
    def _persist(record: MarketRecord) -> None:
        initialize_database()

        with get_connection() as connection:
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

    def get_realtime_quote(
        self,
        symbol: str,
        persist: bool = True,
    ) -> RealtimeQuoteResponse:
        code, canonical_symbol = _normalize_symbol(symbol)
        provider_runs: list[ProviderRun] = []

        started = perf_counter()

        try:
            record = self._create_realtime_record(
                code=code,
                symbol=canonical_symbol,
            )

            provider_runs.append(
                ProviderRun(
                    provider="Tencent / AKShare Realtime",
                    success=True,
                    record_count=1,
                    latency_ms=round(
                        (perf_counter() - started) * 1000
                    ),
                )
            )

            if persist:
                self._persist(record)

            return RealtimeQuoteResponse(
                symbol=canonical_symbol,
                record=record,
                provider_runs=provider_runs,
                is_realtime=True,
                fallback_used=False,
            )

        except Exception as exc:
            provider_runs.append(
                ProviderRun(
                    provider="Tencent / AKShare Realtime",
                    success=False,
                    record_count=0,
                    latency_ms=round(
                        (perf_counter() - started) * 1000
                    ),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )

        fallback_started = perf_counter()

        try:
            record = self._create_fallback_record(canonical_symbol)

            provider_runs.append(
                ProviderRun(
                    provider="Verified Daily Bars Fallback",
                    success=True,
                    record_count=1,
                    latency_ms=round(
                        (perf_counter() - fallback_started) * 1000
                    ),
                )
            )

        except Exception as exc:
            provider_runs.append(
                ProviderRun(
                    provider="Verified Daily Bars Fallback",
                    success=False,
                    record_count=0,
                    latency_ms=round(
                        (perf_counter() - fallback_started) * 1000
                    ),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )

            raise RuntimeError(
                "实时报价和最新日线降级服务均失败"
            ) from exc

        if persist:
            self._persist(record)

        return RealtimeQuoteResponse(
            symbol=canonical_symbol,
            record=record,
            provider_runs=provider_runs,
            is_realtime=False,
            fallback_used=True,
            warning=(
                "实时行情源不可用，当前返回经交叉核验的最近交易日"
                "收盘数据，不代表实时价格。"
            ),
        )