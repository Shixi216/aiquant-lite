from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import date, datetime, time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from database.db import get_connection, initialize_database
from data_hub.providers.tushare_provider import TushareProvider
from data_hub.services.full_market_common import parse_date, stable_hash


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    return parse_date(value)


def _available(day: date, hour: int = 16) -> datetime:
    return datetime.combine(day, time(hour=hour), tzinfo=SHANGHAI_TZ)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class FormalHistoryDataRepository:
    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.connection_factory = connection_factory or get_connection

    def _insert(
        self,
        *,
        table: str,
        columns: list[str],
        rows: list[list[Any]],
    ) -> int:
        if not rows:
            return 0
        with self.connection_factory() as connection:
            before = int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            view = "_formal_history_rows"
            connection.register(
                view,
                pd.DataFrame.from_records(rows, columns=columns),
            )
            try:
                quoted = ", ".join(f'"{item}"' for item in columns)
                connection.execute(
                    f'''INSERT INTO "{table}" ({quoted})
                        SELECT {quoted} FROM "{view}"
                        ON CONFLICT DO NOTHING'''
                )
            finally:
                connection.unregister(view)
            after = int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
        return after - before

    def save_adjustment_factors(
        self,
        rows: list[dict[str, Any]],
        *,
        fetched_at: datetime,
    ) -> int:
        values: list[list[Any]] = []
        for raw in rows:
            trade_date = _date(raw.get("trade_date"))
            factor = _number(raw.get("adj_factor"))
            symbol = str(raw.get("ts_code") or "").upper()
            if trade_date is None or factor is None or factor <= 0 or not symbol:
                continue
            available = _available(trade_date)
            digest = stable_hash({"kind": "ADJ_FACTOR", "raw": raw})
            values.append([
                f"adj_{digest[:32]}", symbol, trade_date, factor,
                available, available, available, fetched_at,
                "Tushare Pro", digest, _json(raw),
            ])
        return self._insert(
            table="historical_adjustment_factors",
            columns=[
                "factor_id", "symbol", "trade_date", "adj_factor",
                "event_time", "data_available_time", "data_cutoff",
                "fetched_at", "source", "content_hash", "raw_payload_json",
            ],
            rows=values,
        )

    def materialize_forward_adjusted_bars(
        self,
        *,
        symbols: tuple[str, ...],
        start_date: date,
        end_date: date,
        generated_at: datetime,
    ) -> int:
        if not symbols:
            return 0
        with self.connection_factory() as connection:
            rows = connection.execute(
                """
                WITH raw AS (
                    SELECT *
                    FROM canonical_historical_bars
                    WHERE adjustment_type = 'RAW'
                      AND verification_status <> 'CONFLICT'
                      AND symbol IN (SELECT UNNEST(?::VARCHAR[]))
                      AND trade_date BETWEEN ? AND ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY generated_at DESC, bar_id
                    ) = 1
                ), factors AS (
                    SELECT *
                    FROM historical_adjustment_factors
                    WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
                      AND trade_date BETWEEN ? AND ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY fetched_at DESC, factor_id
                    ) = 1
                ), base AS (
                    SELECT symbol, ARG_MAX(adj_factor, trade_date) AS base_factor
                    FROM factors
                    GROUP BY symbol
                )
                SELECT r.bar_id, r.symbol, r.trade_date, r.event_time,
                       r.data_available_time, r.open, r.high, r.low,
                       r.close, r.volume, r.amount, r.volume_unit,
                       r.amount_unit, r.source_record_ids_json,
                       f.factor_id, f.adj_factor, f.data_available_time,
                       b.base_factor
                FROM raw r
                JOIN factors f
                  ON f.symbol = r.symbol AND f.trade_date = r.trade_date
                JOIN base b ON b.symbol = r.symbol
                ORDER BY r.symbol, r.trade_date
                """,
                [symbols, start_date, end_date, symbols, start_date, end_date],
            ).fetchall()
        values: list[list[Any]] = []
        for row in rows:
            (
                raw_bar_id, symbol, trade_date, event_time,
                raw_available, open_, high, low, close, volume, amount,
                volume_unit, amount_unit, raw_source_ids, factor_id,
                factor, factor_available, base_factor,
            ) = row
            ratio = float(factor) / float(base_factor)
            payload = {
                "raw_bar_id": raw_bar_id,
                "factor_id": factor_id,
                "adj_factor": float(factor),
                "base_factor": float(base_factor),
                "adjustment_type": "FORWARD_ADJUSTED",
            }
            digest = stable_hash({
                "symbol": symbol,
                "trade_date": trade_date,
                "ratio": ratio,
                "payload": payload,
            })
            source_ids = (
                json.loads(raw_source_ids)
                if isinstance(raw_source_ids, str)
                else list(raw_source_ids)
            )
            source_ids.append(factor_id)
            available = max(raw_available, factor_available)
            values.append([
                f"hbar_qfq_{digest[:32]}", symbol, trade_date,
                event_time, available, available, generated_at,
                "FORWARD_ADJUSTED", float(open_) * ratio,
                float(high) * ratio, float(low) * ratio,
                float(close) * ratio, volume, amount, volume_unit,
                amount_unit, "Tushare Pro / QFQ derived",
                _json(source_ids), _json([]), "SINGLE_SOURCE", 0.5,
                digest, "formal-history-qfq-v1", _json(payload),
            ])
        return self._insert(
            table="canonical_historical_bars",
            columns=[
                "bar_id", "symbol", "trade_date", "event_time",
                "data_available_time", "data_cutoff", "generated_at",
                "adjustment_type", "open", "high", "low", "close",
                "volume", "amount", "volume_unit", "amount_unit",
                "primary_source", "source_record_ids_json",
                "verification_source_ids_json", "verification_status",
                "confidence", "content_hash", "algorithm_version",
                "raw_payload_json",
            ],
            rows=values,
        )
    def save_security_statuses(
        self,
        *,
        suspensions: list[dict[str, Any]],
        name_changes: list[dict[str, Any]],
        fetched_at: datetime,
    ) -> int:
        values: list[list[Any]] = []
        suspension_intervals: list[tuple[str, date, date | None, dict[str, Any]]] = []
        event_rows = [row for row in suspensions if row.get("trade_date")]
        legacy_rows = [row for row in suspensions if row.get("suspend_date")]
        for raw in legacy_rows:
            symbol = str(raw.get("ts_code") or "").upper()
            start = _date(raw.get("suspend_date"))
            if symbol and start is not None:
                suspension_intervals.append((
                    symbol,
                    start,
                    _date(raw.get("resume_date")),
                    raw,
                ))
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in event_rows:
            symbol = str(raw.get("ts_code") or "").upper()
            if symbol:
                grouped.setdefault(symbol, []).append(raw)
        for symbol, items in grouped.items():
            opened: tuple[date, dict[str, Any]] | None = None
            for raw in sorted(items, key=lambda item: str(item.get("trade_date"))):
                day = _date(raw.get("trade_date"))
                event_type = str(raw.get("suspend_type") or "").upper()
                if day is None:
                    continue
                if event_type == "S" and opened is None:
                    opened = (day, raw)
                elif event_type == "R" and opened is not None:
                    suspension_intervals.append((
                        symbol,
                        opened[0],
                        day,
                        {"suspend": opened[1], "resume": raw},
                    ))
                    opened = None
            if opened is not None:
                suspension_intervals.append((
                    symbol,
                    opened[0],
                    None,
                    {"suspend": opened[1]},
                ))
        for symbol, start, end, raw in suspension_intervals:
            available = _available(start, 9)
            digest = stable_hash({"kind": "SUSPENDED", "raw": raw})
            values.append([
                f"status_{digest[:32]}", symbol, "SUSPENDED", True,
                start, end, available, available, available, fetched_at,
                "Tushare Pro", digest, _json(raw),
            ])
        for raw in name_changes:
            name = str(raw.get("name") or "")
            if "ST" not in name.upper():
                continue
            symbol = str(raw.get("ts_code") or "").upper()
            start = _date(raw.get("start_date"))
            end = _date(raw.get("end_date"))
            if not symbol or start is None:
                continue
            available = _available(start, 9)
            digest = stable_hash({"kind": "ST", "raw": raw})
            values.append([
                f"status_{digest[:32]}", symbol, "ST", True,
                start, end, available, available, available, fetched_at,
                "Tushare Pro", digest, _json(raw),
            ])
        return self._insert(
            table="historical_security_statuses",
            columns=[
                "status_id", "symbol", "status_type", "status_value",
                "effective_start", "effective_end", "event_time",
                "data_available_time", "data_cutoff", "fetched_at",
                "source", "content_hash", "raw_payload_json",
            ],
            rows=values,
        )

    def save_benchmark_bars(
        self,
        rows: list[dict[str, Any]],
        *,
        benchmark_code: str,
        benchmark_name: str | None,
        benchmark_type: str,
        source: str,
        fetched_at: datetime,
    ) -> int:
        values: list[list[Any]] = []
        for raw in rows:
            trade_date = _date(raw.get("trade_date") or raw.get("日期"))
            close = _number(raw.get("close") if "close" in raw else raw.get("收盘"))
            if trade_date is None or close is None or close <= 0:
                continue
            available = _available(trade_date)
            digest = stable_hash({
                "kind": benchmark_type,
                "code": benchmark_code,
                "raw": raw,
            })
            values.append([
                f"bench_{digest[:32]}", benchmark_code, benchmark_name,
                benchmark_type, trade_date,
                _number(raw.get("open") if "open" in raw else raw.get("开盘")),
                _number(raw.get("high") if "high" in raw else raw.get("最高")),
                _number(raw.get("low") if "low" in raw else raw.get("最低")),
                close,
                _number(raw.get("vol") if "vol" in raw else raw.get("成交量")),
                _number(raw.get("amount") if "amount" in raw else raw.get("成交额")),
                available, available, available, fetched_at,
                source, digest, _json(raw),
            ])
        return self._insert(
            table="historical_benchmark_bars",
            columns=[
                "bar_id", "benchmark_code", "benchmark_name",
                "benchmark_type", "trade_date", "open", "high", "low",
                "close", "volume", "amount", "event_time",
                "data_available_time", "data_cutoff", "fetched_at",
                "source", "content_hash", "raw_payload_json",
            ],
            rows=values,
        )

    def save_risk_events(
        self,
        records: list[Any],
        *,
        fetched_at: datetime,
    ) -> int:
        values: list[list[Any]] = []
        for record in records:
            title = str(record.data.get("title") or "").strip()
            if not title:
                continue
            available = record.event_time
            digest = record.content_hash or stable_hash({
                "symbol": record.symbol,
                "event_time": record.event_time,
                "title": title,
                "source": record.source_name,
            })
            values.append([
                f"risk_{digest[:32]}", record.symbol, "ANNOUNCEMENT",
                title, record.event_time, available, available, fetched_at,
                record.source_name,
                str(record.source_url) if record.source_url else None,
                digest, _json(record.data),
            ])
        return self._insert(
            table="historical_risk_events",
            columns=[
                "event_id", "symbol", "event_type", "title",
                "event_time", "data_available_time", "data_cutoff",
                "fetched_at", "source", "source_url", "content_hash",
                "raw_payload_json",
            ],
            rows=values,
        )
    def save_industry_memberships(
        self,
        rows: list[dict[str, Any]],
        *,
        industry_name: str | None,
        fetched_at: datetime,
    ) -> int:
        values: list[list[Any]] = []
        for raw in rows:
            symbol = str(raw.get("ts_code") or "").upper()
            code = str(raw.get("l1_code") or "").upper()
            start = _date(raw.get("in_date"))
            end = _date(raw.get("out_date"))
            if not symbol or not code or start is None:
                continue
            available = _available(start)
            digest = stable_hash({"kind": "SW_MEMBER", "raw": raw})
            values.append([
                f"membership_{digest[:32]}", symbol, code,
                raw.get("l1_name") or industry_name, start, end,
                available, available, available, fetched_at,
                "Tushare Pro / SW2021", digest, _json(raw),
            ])
        return self._insert(
            table="historical_industry_memberships",
            columns=[
                "membership_id", "symbol", "industry_code",
                "industry_name", "valid_from", "valid_to", "event_time",
                "data_available_time", "data_cutoff", "fetched_at",
                "source", "content_hash", "raw_payload_json",
            ],
            rows=values,
        )


class FormalHistoryDataBackfillService:
    def __init__(
        self,
        *,
        provider: TushareProvider | None = None,
        repository: FormalHistoryDataRepository | None = None,
        industry_fetcher: Callable[[str], list[dict[str, Any]]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider or TushareProvider()
        self.repository = repository or FormalHistoryDataRepository()
        self.industry_fetcher = industry_fetcher or self._fetch_industry
        self.clock = clock or (lambda: datetime.now(tz=SHANGHAI_TZ))

    @staticmethod
    def _fetch_industry(code: str) -> list[dict[str, Any]]:
        import akshare as ak

        frame = ak.index_hist_sw(symbol=code.split(".", 1)[0], period="day")
        return frame.to_dict(orient="records")

    def run(
        self,
        *,
        start_date: date,
        end_date: date,
        adjustment_symbols: tuple[str, ...] = (),
        include_industries: bool = True,
    ) -> dict[str, Any]:
        if start_date > end_date:
            raise ValueError("start_date must not be later than end_date")
        initialize_database()
        fetched_at = self.clock()
        start = start_date.strftime("%Y%m%d")
        end = end_date.strftime("%Y%m%d")
        counts = {
            "adjustment_factors": 0,
            "security_statuses": 0,
            "csi300_bars": 0,
            "industry_bars": 0,
            "industry_memberships": 0,
        }
        for symbol in adjustment_symbols:
            counts["adjustment_factors"] += self.repository.save_adjustment_factors(
                self.provider.get_adjustment_factors(symbol, start, end),
                fetched_at=fetched_at,
            )
        counts["security_statuses"] = self.repository.save_security_statuses(
            suspensions=self.provider.get_suspension_history(start, end),
            name_changes=self.provider.get_name_change_history(start, end),
            fetched_at=fetched_at,
        )
        counts["csi300_bars"] = self.repository.save_benchmark_bars(
            self.provider.get_index_daily("000300.SH", start, end),
            benchmark_code="000300.SH",
            benchmark_name="沪深300",
            benchmark_type="CSI300",
            source="Tushare Pro",
            fetched_at=fetched_at,
        )
        if include_industries:
            for industry in self.provider.get_sw_industry_indices():
                code = str(industry["index_code"])
                name = str(industry.get("industry_name") or code)
                members = self.provider.get_index_members(code)
                counts["industry_memberships"] += (
                    self.repository.save_industry_memberships(
                        members,
                        industry_name=name,
                        fetched_at=fetched_at,
                    )
                )
                rows = [
                    row
                    for row in self.industry_fetcher(code)
                    if start_date <= (_date(row.get("日期")) or date.min) <= end_date
                ]
                counts["industry_bars"] += self.repository.save_benchmark_bars(
                    rows,
                    benchmark_code=code,
                    benchmark_name=name,
                    benchmark_type="INDUSTRY",
                    source="AKShare / Shenwan",
                    fetched_at=fetched_at,
                )
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "fetched_at": fetched_at.isoformat(),
            "counts": counts,
            "production_config_updated": False,
            "orders_created": 0,
            "positions_changed": 0,
        }


__all__ = [
    "FormalHistoryDataBackfillService",
    "FormalHistoryDataRepository",
]