from __future__ import annotations

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from data_hub.schemas.market import DataType, MarketRecord, SourceLevel


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _normalize_date(value: str) -> str:
    normalized = value.replace("-", "").strip()

    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(f"日期格式必须是 YYYYMMDD 或 YYYY-MM-DD：{value}")

    datetime.strptime(normalized, "%Y%m%d")
    return normalized


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


class AKShareProvider:
    """AKShare adapter backed by public market-data sources."""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    def _fetch_daily(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        return ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[MarketRecord]:
        code, canonical_symbol = _normalize_symbol(symbol)
        start = _normalize_date(start_date)
        end = _normalize_date(end_date)

        if start > end:
            raise ValueError("start_date 不能晚于 end_date")

        frame = self._fetch_daily(
            code=code,
            start_date=start,
            end_date=end,
        )

        if frame.empty:
            return []

        required = {"日期", "开盘", "最高", "最低", "收盘", "成交量"}
        missing = required.difference(frame.columns)

        if missing:
            raise RuntimeError(f"AKShare 缺少字段：{sorted(missing)}")

        records: list[MarketRecord] = []

        for _, row in frame.sort_values("日期").iterrows():
            trade_date = pd.Timestamp(row["日期"]).strftime("%Y%m%d")

            payload: dict[str, object] = {
                "symbol": canonical_symbol,
                "trade_date": trade_date,
                "open": _to_float(row.get("开盘")),
                "high": _to_float(row.get("最高")),
                "low": _to_float(row.get("最低")),
                "close": _to_float(row.get("收盘")),
                "volume": _to_float(row.get("成交量")),
                "amount": _to_float(row.get("成交额")),
                "amplitude": _to_float(row.get("振幅")),
                "pct_chg": _to_float(row.get("涨跌幅")),
                "change": _to_float(row.get("涨跌额")),
                "turnover_rate": _to_float(row.get("换手率")),
            }

            hash_payload = {
                "source": "AKShare / Eastmoney",
                "symbol": canonical_symbol,
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
                    symbol=canonical_symbol,
                    data_type=DataType.DAILY_BAR,
                    event_time=event_time,
                    source_name="AKShare / Eastmoney",
                    source_level=SourceLevel.PUBLIC_WEB,
                    verified=False,
                    content_hash=_content_hash(hash_payload),
                    data=payload,
                )
            )

        return records