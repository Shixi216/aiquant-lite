from __future__ import annotations

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import tushare as ts
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _normalize_date(value: str) -> str:
    normalized = value.replace("-", "").strip()

    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(f"日期格式必须是 YYYYMMDD 或 YYYY-MM-DD：{value}")

    datetime.strptime(normalized, "%Y%m%d")
    return normalized


def _normalize_ts_code(value: str) -> str:
    normalized = value.strip().upper()

    if "." not in normalized:
        if normalized.startswith(("5", "6", "9")):
            normalized = f"{normalized}.SH"
        else:
            normalized = f"{normalized}.SZ"

    code, exchange = normalized.split(".", 1)

    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效股票代码：{value}")

    if exchange not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"不支持的交易所：{exchange}")

    return f"{code}.{exchange}"


def _to_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TushareProvider:
    """Tushare Pro structured-data adapter."""

    def __init__(self, token: str | None = None) -> None:
        resolved_token = token or settings.tushare_token

        if not resolved_token or not resolved_token.strip():
            raise RuntimeError("TUSHARE_TOKEN 未配置")

        self._client = ts.pro_api(resolved_token.strip())

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    def _fetch_daily(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        return self._client.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[MarketRecord]:
        ts_code = _normalize_ts_code(symbol)
        start = _normalize_date(start_date)
        end = _normalize_date(end_date)

        if start > end:
            raise ValueError("start_date 不能晚于 end_date")

        frame = self._fetch_daily(
            ts_code=ts_code,
            start_date=start,
            end_date=end,
        )

        if frame.empty:
            return []

        records: list[MarketRecord] = []

        for _, row in frame.sort_values("trade_date").iterrows():
            trade_date = str(row["trade_date"])

            payload: dict[str, object] = {
                "ts_code": str(row["ts_code"]),
                "trade_date": trade_date,
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "pre_close": _to_float(row.get("pre_close")),
                "change": _to_float(row.get("change")),
                "pct_chg": _to_float(row.get("pct_chg")),
                "vol": _to_float(row.get("vol")),
                "amount": _to_float(row.get("amount")),
            }

            hash_payload = {
                "source": "Tushare Pro",
                "symbol": ts_code,
                "data_type": DataType.DAILY_BAR.value,
                "trade_date": trade_date,
                "data": payload,
            }

            event_time = datetime.strptime(
                trade_date,
                "%Y%m%d",
            ).replace(
                hour=15,
                minute=0,
                second=0,
                tzinfo=SHANGHAI_TZ,
            )

            records.append(
                MarketRecord(
                    symbol=ts_code,
                    data_type=DataType.DAILY_BAR,
                    event_time=event_time,
                    source_name="Tushare Pro",
                    source_level=SourceLevel.STRUCTURED,
                    verified=False,
                    content_hash=_content_hash(hash_payload),
                    data=payload,
                )
            )

        return records