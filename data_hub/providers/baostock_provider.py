from __future__ import annotations

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import baostock as bs

from data_hub.schemas.market import DataType, MarketRecord, SourceLevel


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _normalize_date(value: str) -> str:
    normalized = value.replace("-", "").strip()

    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(f"日期格式必须是 YYYYMMDD 或 YYYY-MM-DD：{value}")

    datetime.strptime(normalized, "%Y%m%d")
    return normalized


def _format_date(value: str) -> str:
    normalized = _normalize_date(value)
    return datetime.strptime(normalized, "%Y%m%d").strftime("%Y-%m-%d")


def _normalize_symbol(value: str) -> tuple[str, str]:
    normalized = value.strip().upper()
    code = normalized.split(".", 1)[0]

    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效股票代码：{value}")

    if "." in normalized:
        exchange = normalized.split(".", 1)[1]
    elif code.startswith(("5", "6", "9")):
        exchange = "SH"
    elif code.startswith(("4", "8", "920")):
        exchange = "BJ"
    else:
        exchange = "SZ"

    if exchange not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"不支持的交易所：{exchange}")

    if exchange == "BJ":
        raise ValueError("当前 BaoStock Provider 暂不支持北交所代码")

    baostock_code = f"{exchange.lower()}.{code}"
    canonical_symbol = f"{code}.{exchange}"

    return baostock_code, canonical_symbol


def _to_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
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


class BaoStockProvider:
    """BaoStock historical-data adapter."""

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjustment_type: str = "RAW",
    ) -> list[MarketRecord]:
        baostock_code, canonical_symbol = _normalize_symbol(symbol)
        start = _format_date(start_date)
        end = _format_date(end_date)

        if start > end:
            raise ValueError("start_date 不能晚于 end_date")

        adjustflag = {
            "RAW": "3",
            "FORWARD_ADJUSTED": "2",
            "BACKWARD_ADJUSTED": "1",
        }.get(adjustment_type)
        if adjustflag is None:
            raise ValueError(f"unsupported adjustment_type: {adjustment_type}")

        login_result = bs.login()

        if login_result.error_code != "0":
            raise RuntimeError(
                "BaoStock 登录失败："
                f"{login_result.error_code} {login_result.error_msg}"
            )

        try:
            query = bs.query_history_k_data_plus(
                baostock_code,
                (
                    "date,code,open,high,low,close,"
                    "volume,amount,adjustflag"
                ),
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag=adjustflag,
            )

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

            records: list[MarketRecord] = []

            for row in rows:
                trade_date = row["date"].replace("-", "")

                payload: dict[str, object] = {
                    "symbol": canonical_symbol,
                    "raw_code": row.get("code"),
                    "trade_date": trade_date,
                    "open": _to_float(row.get("open")),
                    "high": _to_float(row.get("high")),
                    "low": _to_float(row.get("low")),
                    "close": _to_float(row.get("close")),
                    "volume": _to_float(row.get("volume")),
                    "amount": _to_float(row.get("amount")),
                    "adjustflag": row.get("adjustflag"),
                    "adjustment_type": adjustment_type,
                    "provider_adjustment": adjustflag,
                    "volume_unit": "SHARES",
                    "amount_unit": "CNY",
                }

                hash_payload = {
                    "source": "BaoStock",
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

                digest = _content_hash(hash_payload)
                records.append(
                    MarketRecord(
                        record_id=f"raw_daily_{digest[:32]}",
                        symbol=canonical_symbol,
                        data_type=DataType.DAILY_BAR,
                        event_time=event_time,
                        source_name="BaoStock",
                        source_level=SourceLevel.STRUCTURED,
                        verified=False,
                        content_hash=digest,
                        data=payload,
                    )
                )

            return records

        finally:
            bs.logout()
