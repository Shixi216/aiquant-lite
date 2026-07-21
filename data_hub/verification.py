from __future__ import annotations

from dataclasses import dataclass

from data_hub.schemas.market import MarketRecord


PRICE_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    trade_date: str
    sources: tuple[str, ...]
    matched_fields: tuple[str, ...]
    mismatches: dict[str, dict[str, float | None]]


def verify_daily_bars(
    records: list[MarketRecord],
    tolerance: float = 0.005,
) -> VerificationResult:
    if len(records) < 2:
        raise ValueError("至少需要两个数据源才能执行交叉核验")

    symbols = {record.symbol for record in records}

    if len(symbols) != 1:
        raise ValueError(f"记录股票代码不一致：{sorted(symbols)}")

    trade_dates = {
        str(record.data.get("trade_date"))
        for record in records
    }

    if len(trade_dates) != 1:
        raise ValueError(f"记录交易日期不一致：{sorted(trade_dates)}")

    matched_fields: list[str] = []
    mismatches: dict[str, dict[str, float | None]] = {}

    for field in PRICE_FIELDS:
        values = {
            record.source_name: (
                float(record.data[field])
                if record.data.get(field) is not None
                else None
            )
            for record in records
        }

        numeric_values = [
            value
            for value in values.values()
            if value is not None
        ]

        if len(numeric_values) < 2:
            mismatches[field] = values
            continue

        if max(numeric_values) - min(numeric_values) <= tolerance:
            matched_fields.append(field)
        else:
            mismatches[field] = values

    verified = len(matched_fields) == len(PRICE_FIELDS)

    return VerificationResult(
        verified=verified,
        trade_date=next(iter(trade_dates)),
        sources=tuple(record.source_name for record in records),
        matched_fields=tuple(matched_fields),
        mismatches=mismatches,
    )