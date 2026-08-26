from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from config.settings import settings
from data_hub.providers.tushare_provider import classify_tushare_error
from trading.research.sentiment.v3.feature_registry import (
    FEATURE_REGISTRY_V1,
    FEATURE_REGISTRY_VERSION,
    FeatureDefinition,
)
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.schemas import SnapshotModel


SHANGHAI = ZoneInfo("Asia/Shanghai")
REPLAY_DATABASE_NAME = "sentiment_v3_replay.duckdb"
REPLAY_SCHEMA_VERSION = "SENTIMENT_V3_HISTORICAL_REPLAY_SCHEMA_V1"
REPLAY_ALGORITHM_VERSION = "sentiment-v3-historical-replay-s3b0-1.0.1"
CUTOFF_POLICY_VERSION = "PREOPEN_REPLAY_CUTOFF_V1"
REPLAY_DATASET_VERSION = "S3B_REPLAY_DATASET_V1"
RESEARCH_SET_VERSION = "S3B_RESEARCH_SET_V1"
AVAILABILITY_MANIFEST_VERSION = "HistoricalReplayAvailabilityManifestV1"
DECISION_CUTOFF_TIME = time(9, 10)
EXECUTABLE_ENTRY_TIME = time(9, 30)
EOD_AVAILABLE_TIME = time(18, 0)
MARGIN_AVAILABLE_TIME = time(9, 0)


class ReplayStatus(StrEnum):
    REPLAY_READY = "REPLAY_READY"
    REPLAY_SOURCE_BACKFILLABLE = "REPLAY_SOURCE_BACKFILLABLE"
    FORWARD_ONLY_DUE_TO_SOURCE = "FORWARD_ONLY_DUE_TO_SOURCE"
    BLOCKED_BY_DATA_QUALITY = "BLOCKED_BY_DATA_QUALITY"


class SourceKind(StrEnum):
    SECURITY_MASTER = "SECURITY_MASTER"
    TRADE_CALENDAR = "TRADE_CALENDAR"
    DAILY_REFERENCE = "DAILY_REFERENCE"
    DAILY_BASIC = "DAILY_BASIC"
    STK_LIMIT = "STK_LIMIT"
    MARGIN = "MARGIN"
    DRAGON_TIGER = "DRAGON_TIGER"


class HistoricalFeatureAvailability(SnapshotModel):
    feature_name: str
    module: str
    required_source: list[str]
    required_raw_fields: list[str]
    historical_source_exists: bool
    historical_date_range: str | None = None
    can_reconstruct_deterministically: bool
    available_time_policy: str
    pit_safe: bool
    replay_status: ReplayStatus
    blocking_reason: str | None = None


class HistoricalReplayAvailabilityManifest(SnapshotModel):
    manifest_version: str = AVAILABILITY_MANIFEST_VERSION
    registry_version: str = FEATURE_REGISTRY_VERSION
    registry_file_sha256: str
    registry_content_hash: str
    label_spec_sha256: str
    evaluation_sha256: str
    raw_date_from: date
    raw_date_to: date
    raw_trading_dates: int
    features: list[HistoricalFeatureAvailability]
    content_hash: str


class HistoricalResearchSet(SnapshotModel):
    research_set_version: str = RESEARCH_SET_VERSION
    manifest_hash: str
    feature_names: list[str]
    excluded_features: dict[str, str]
    content_hash: str


@dataclass(frozen=True)
class ReplayBuildMetrics:
    market_seconds: float = 0.0
    stock_packet_seconds: float = 0.0
    label_seconds: float = 0.0
    dataset_seconds: float = 0.0
    db_write_seconds: float = 0.0


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def default_replay_database() -> Path:
    return Path(settings.opc_database_path).with_name(REPLAY_DATABASE_NAME)


def _source_dependencies(definition: FeatureDefinition) -> tuple[list[str], list[str], str]:
    field = definition.source_field
    source_type = definition.source_snapshot_type
    if source_type == "MARKET":
        fields = ["close", "previous_close", "amount", "historical universe"]
        return ["canonical_historical_bars", "Tushare daily", "stock_basic", "security_status"], fields, (
            "T EOD 18:00 inferred; visible by next-session 09:10"
        )
    if source_type == "STOCK_CAPITAL":
        return ["canonical_historical_bars"], ["amount"], (
            "T EOD 18:00 inferred; visible by next-session 09:10"
        )
    if source_type == "MARGIN_FINANCING":
        return ["Tushare margin_detail"], [field, "previous trading-day margin_detail"], (
            "T data available next trading session 09:00 inferred"
        )
    if source_type == "DRAGON_TIGER":
        return ["Tushare top_list/top_inst"], [field, "successful date coverage"], (
            "T data available T 18:00 inferred"
        )
    if field in {"turnover_rate", "volume_ratio"}:
        return ["Tushare daily_basic"], [field], (
            "T EOD 18:00 inferred; visible by next-session 09:10"
        )
    if field in {"is_limit_up", "is_limit_down", "is_broken_board"}:
        return (
            ["canonical_historical_bars", "Tushare stk_limit"],
            ["open", "high", "low", "close", "up_limit", "down_limit"],
            "T EOD 18:00 inferred; visible by next-session 09:10",
        )
    return (
        ["canonical_historical_bars", "Tushare daily", "historical_security_statuses"],
        ["open", "high", "low", "close", "volume", "amount", "previous bar"],
        "T EOD 18:00 inferred; visible by next-session 09:10",
    )


class HistoricalReplayStore:
    """Single-writer, research-only DuckDB boundary for historical REPLAY data."""

    def __init__(
        self,
        replay_database: str | Path | None = None,
        source_database: str | Path | None = None,
    ) -> None:
        self.replay_database = Path(replay_database or default_replay_database())
        self.source_database = Path(source_database or settings.opc_database_path)
        self.replay_database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            self._create_schema(connection)

    def connection(self):
        return duckdb.connect(str(self.replay_database))

    def attach_source(self, connection) -> None:
        escaped = str(self.source_database).replace("'", "''")
        attached = {
            row[1] for row in connection.execute("PRAGMA database_list").fetchall()
        }
        if "source" not in attached:
            connection.execute(f"ATTACH '{escaped}' AS source (READ_ONLY)")

    @staticmethod
    def _create_schema(connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_security_master (
                symbol VARCHAR PRIMARY KEY,
                name VARCHAR,
                market VARCHAR,
                exchange VARCHAR NOT NULL,
                list_status VARCHAR NOT NULL,
                list_date DATE,
                delist_date DATE,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                content_hash VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_trade_calendar (
                trade_date DATE PRIMARY KEY,
                is_open BOOLEAN NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                content_hash VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_source_checkpoints (
                source_kind VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                status VARCHAR NOT NULL,
                record_count BIGINT NOT NULL,
                latency_ms DOUBLE NOT NULL,
                error_type VARCHAR,
                error_message_sanitized VARCHAR,
                content_hash VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(source_kind, trade_date)
            );
            CREATE TABLE IF NOT EXISTS replay_daily_reference (
                trade_date DATE NOT NULL,
                symbol VARCHAR NOT NULL,
                previous_close DOUBLE,
                return_pct DOUBLE,
                source VARCHAR NOT NULL,
                source_timestamp TIMESTAMPTZ NOT NULL,
                available_time TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                content_hash VARCHAR NOT NULL,
                PRIMARY KEY(trade_date, symbol)
            );
            CREATE TABLE IF NOT EXISTS replay_daily_basic (
                trade_date DATE NOT NULL,
                symbol VARCHAR NOT NULL,
                turnover_rate DOUBLE,
                volume_ratio DOUBLE,
                source VARCHAR NOT NULL,
                source_timestamp TIMESTAMPTZ NOT NULL,
                available_time TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                content_hash VARCHAR NOT NULL,
                PRIMARY KEY(trade_date, symbol)
            );
            CREATE TABLE IF NOT EXISTS replay_stk_limit (
                trade_date DATE NOT NULL,
                symbol VARCHAR NOT NULL,
                up_limit DOUBLE,
                down_limit DOUBLE,
                source VARCHAR NOT NULL,
                source_timestamp TIMESTAMPTZ NOT NULL,
                available_time TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                content_hash VARCHAR NOT NULL,
                PRIMARY KEY(trade_date, symbol)
            );
            CREATE TABLE IF NOT EXISTS replay_margin (
                trade_date DATE NOT NULL,
                symbol VARCHAR NOT NULL,
                margin_balance DOUBLE,
                margin_buy DOUBLE,
                margin_repay DOUBLE,
                short_balance DOUBLE,
                short_sell DOUBLE,
                short_repay DOUBLE,
                total_margin_balance DOUBLE,
                source VARCHAR NOT NULL,
                source_timestamp TIMESTAMPTZ NOT NULL,
                available_time TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                content_hash VARCHAR NOT NULL,
                PRIMARY KEY(trade_date, symbol)
            );
            CREATE TABLE IF NOT EXISTS replay_margin_market (
                trade_date DATE PRIMARY KEY,
                market_margin_total DOUBLE,
                source VARCHAR NOT NULL,
                source_timestamp TIMESTAMPTZ NOT NULL,
                available_time TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                content_hash VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_dragon_tiger_events (
                trade_date DATE NOT NULL,
                symbol VARCHAR NOT NULL,
                reasons_json JSON NOT NULL,
                buy_amount DOUBLE,
                sell_amount DOUBLE,
                net_amount DOUBLE,
                institution_buy_amount DOUBLE,
                institution_sell_amount DOUBLE,
                institution_net_amount DOUBLE,
                seat_count INTEGER,
                institution_seat_count INTEGER,
                source VARCHAR NOT NULL,
                source_timestamp TIMESTAMPTZ NOT NULL,
                available_time TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                content_hash VARCHAR NOT NULL,
                PRIMARY KEY(trade_date, symbol)
            );
            CREATE TABLE IF NOT EXISTS replay_runs (
                run_id VARCHAR PRIMARY KEY,
                run_type VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                date_from DATE,
                date_to DATE,
                metrics_json JSON NOT NULL,
                content_hash VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_dataset_metadata (
                dataset_id VARCHAR PRIMARY KEY,
                dataset_version VARCHAR NOT NULL,
                registry_sha VARCHAR NOT NULL,
                label_spec_sha VARCHAR NOT NULL,
                evaluation_sha VARCHAR NOT NULL,
                cutoff_policy_version VARCHAR NOT NULL,
                date_from DATE NOT NULL,
                date_to DATE NOT NULL,
                trading_date_count INTEGER NOT NULL,
                packet_count BIGINT NOT NULL,
                symbol_count INTEGER NOT NULL,
                feature_count INTEGER NOT NULL,
                record_source VARCHAR NOT NULL,
                content_hash VARCHAR NOT NULL,
                payload_json JSON NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            );
            """
        )

    def raw_range(self) -> tuple[date, date, int, int, int]:
        with self.connection() as connection:
            self.attach_source(connection)
            row = connection.execute(
                """
                SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date),
                       COUNT(DISTINCT symbol), COUNT(*)
                FROM source.canonical_historical_bars
                WHERE adjustment_type = 'RAW' AND verification_status <> 'CONFLICT'
                """
            ).fetchone()
        return row

    def completed_dates(self, source: SourceKind) -> set[date]:
        with self.connection() as connection:
            return {
                row[0]
                for row in connection.execute(
                    """
                    SELECT trade_date FROM replay_source_checkpoints
                    WHERE source_kind = ? AND status = 'COMPLETE'
                    """,
                    [source.value],
                ).fetchall()
            }

    def checkpoint(
        self,
        *,
        source: SourceKind,
        trade_date: date,
        status: str,
        record_count: int,
        latency_ms: float,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(tz=SHANGHAI)
        payload = {
            "source": source.value,
            "trade_date": trade_date,
            "status": status,
            "record_count": record_count,
            "error_type": error_type,
        }
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO replay_source_checkpoints
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    source.value,
                    trade_date,
                    status,
                    record_count,
                    latency_ms,
                    error_type,
                    error_message,
                    _sha(payload),
                    now,
                ],
            )

    def insert_frame(self, table: str, frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        with self.connection() as connection:
            connection.register("_replay_batch", frame)
            try:
                connection.execute(
                    f"INSERT INTO {table} SELECT * FROM _replay_batch ON CONFLICT DO NOTHING"
                )
            finally:
                connection.unregister("_replay_batch")
        return len(frame)

    def source_date_complete(self, source: SourceKind, dates: Iterable[date]) -> bool:
        expected = set(dates)
        return expected.issubset(self.completed_dates(source))


class TushareReplayBackfill:
    """Sequential per-trade-date source backfill with checkpoint/resume semantics."""

    def __init__(self, store: HistoricalReplayStore) -> None:
        self.store = store

    @staticmethod
    def _client():
        if not settings.tushare_token or not settings.tushare_token.strip():
            raise RuntimeError("Tushare token is not configured")
        import tushare as ts

        return ts.pro_api(settings.tushare_token.strip())

    @staticmethod
    def _value(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _safe_error(exc: Exception) -> tuple[str, str]:
        return classify_tushare_error(exc)

    def bootstrap_reference_data(self, *, start: date, end: date) -> dict[str, int]:
        client = self._client()
        now = datetime.now(tz=SHANGHAI)
        frames = []
        for status in ("L", "D", "P"):
            frame = client.stock_basic(
                exchange="",
                list_status=status,
                fields=(
                    "ts_code,name,market,exchange,list_status,list_date,delist_date"
                ),
            )
            frames.append(frame)
        stocks = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
        rows = []
        for item in stocks.to_dict(orient="records"):
            symbol = str(item["ts_code"])
            payload = {
                "symbol": symbol,
                "name": None if pd.isna(item.get("name")) else str(item.get("name")),
                "market": None if pd.isna(item.get("market")) else str(item.get("market")),
                "exchange": str(item.get("exchange") or symbol.split(".")[-1]),
                "list_status": str(item.get("list_status") or "UNKNOWN"),
                "list_date": _parse_date(item.get("list_date")),
                "delist_date": _parse_date(item.get("delist_date")),
            }
            rows.append(
                [
                    *payload.values(),
                    "Tushare Pro stock_basic",
                    now,
                    _sha(payload),
                ]
            )
        stock_frame = pd.DataFrame(
            rows,
            columns=[
                "symbol",
                "name",
                "market",
                "exchange",
                "list_status",
                "list_date",
                "delist_date",
                "source",
                "ingested_at",
                "content_hash",
            ],
        )
        with self.store.connection() as connection:
            connection.register("_security_master", stock_frame)
            connection.execute(
                """
                INSERT INTO replay_security_master SELECT * FROM _security_master
                ON CONFLICT(symbol) DO NOTHING
                """
            )
            connection.unregister("_security_master")
        calendar = client.trade_cal(
            exchange="",
            start_date=(start - timedelta(days=40)).strftime("%Y%m%d"),
            end_date=(end + timedelta(days=80)).strftime("%Y%m%d"),
        )
        calendar_rows = []
        for item in calendar.to_dict(orient="records"):
            day = _parse_date(item.get("cal_date"))
            if day is None:
                continue
            payload = {"trade_date": day, "is_open": bool(int(item.get("is_open", 0)))}
            calendar_rows.append(
                [day, payload["is_open"], "Tushare Pro trade_cal", now, _sha(payload)]
            )
        calendar_frame = pd.DataFrame(
            calendar_rows,
            columns=["trade_date", "is_open", "source", "ingested_at", "content_hash"],
        )
        with self.store.connection() as connection:
            connection.register("_calendar", calendar_frame)
            connection.execute(
                """
                INSERT INTO replay_trade_calendar SELECT * FROM _calendar
                ON CONFLICT(trade_date) DO NOTHING
                """
            )
            connection.unregister("_calendar")
        return {"security_master": len(stock_frame), "calendar": len(calendar_frame)}

    def backfill(
        self,
        *,
        dates: Iterable[date],
        sources: Iterable[SourceKind] = (
            SourceKind.DAILY_REFERENCE,
            SourceKind.DAILY_BASIC,
            SourceKind.STK_LIMIT,
            SourceKind.MARGIN,
            SourceKind.DRAGON_TIGER,
        ),
        force_dates: Iterable[date] = (),
    ) -> dict[str, Any]:
        values = sorted(set(dates))
        force = set(force_dates)
        requested = list(sources)
        client = self._client()
        metrics: dict[str, dict[str, Any]] = {
            item.value: {"calls": 0, "rows": 0, "failures": 0, "seconds": 0.0}
            for item in requested
        }
        with self.store.connection() as connection:
            calendar = [
                row[0]
                for row in connection.execute(
                    "SELECT trade_date FROM replay_trade_calendar WHERE is_open ORDER BY trade_date"
                ).fetchall()
            ]
        next_day = {day: calendar[index + 1] for index, day in enumerate(calendar[:-1])}
        completed = {source: self.store.completed_dates(source) for source in requested}
        for index, day in enumerate(values, start=1):
            for source in requested:
                if day in completed[source] and day not in force:
                    continue
                started = perf_counter()
                try:
                    row_count, calls = self._backfill_one(
                        client=client,
                        source=source,
                        trade_date=day,
                        next_trade_date=next_day.get(day),
                    )
                except Exception as exc:
                    error_type, message = self._safe_error(exc)
                    elapsed = perf_counter() - started
                    self.store.checkpoint(
                        source=source,
                        trade_date=day,
                        status="FAILED",
                        record_count=0,
                        latency_ms=elapsed * 1000,
                        error_type=error_type,
                        error_message=message,
                    )
                    metrics[source.value]["failures"] += 1
                    metrics[source.value]["seconds"] += elapsed
                    continue
                elapsed = perf_counter() - started
                self.store.checkpoint(
                    source=source,
                    trade_date=day,
                    status="COMPLETE",
                    record_count=row_count,
                    latency_ms=elapsed * 1000,
                )
                metrics[source.value]["calls"] += calls
                metrics[source.value]["rows"] += row_count
                metrics[source.value]["seconds"] += elapsed
            if index % 25 == 0:
                print(f"replay source backfill {index}/{len(values)} dates", flush=True)
        return metrics

    def _backfill_one(
        self,
        *,
        client: Any,
        source: SourceKind,
        trade_date: date,
        next_trade_date: date | None,
    ) -> tuple[int, int]:
        key = trade_date.strftime("%Y%m%d")
        now = datetime.now(tz=SHANGHAI)
        source_timestamp = datetime.combine(trade_date, time(15, 0), tzinfo=SHANGHAI)
        eod_available = datetime.combine(trade_date, EOD_AVAILABLE_TIME, tzinfo=SHANGHAI)
        if source == SourceKind.DAILY_REFERENCE:
            frame = client.daily(trade_date=key)
            rows = []
            for item in frame.to_dict(orient="records"):
                symbol = str(item["ts_code"])
                payload = {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "previous_close": self._value(item.get("pre_close")),
                    "return_pct": (
                        None
                        if self._value(item.get("pct_chg")) is None
                        else self._value(item.get("pct_chg")) / 100.0
                    ),
                }
                rows.append(
                    [
                        *payload.values(),
                        "Tushare Pro daily",
                        source_timestamp,
                        eod_available,
                        now,
                        _sha(payload),
                    ]
                )
            target = pd.DataFrame(
                rows, columns=_table_columns(self.store, "replay_daily_reference")
            )
            return self.store.insert_frame("replay_daily_reference", target), 1
        if source == SourceKind.DAILY_BASIC:
            frame = client.daily_basic(
                trade_date=key,
                fields="ts_code,trade_date,turnover_rate,volume_ratio",
            )
            rows = []
            for item in frame.to_dict(orient="records"):
                symbol = str(item["ts_code"])
                payload = {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "turnover_rate": (
                        None
                        if self._value(item.get("turnover_rate")) is None
                        else self._value(item.get("turnover_rate")) / 100.0
                    ),
                    "volume_ratio": self._value(item.get("volume_ratio")),
                }
                rows.append(
                    [
                        *payload.values(),
                        "Tushare Pro daily_basic",
                        source_timestamp,
                        eod_available,
                        now,
                        _sha(payload),
                    ]
                )
            target = pd.DataFrame(rows, columns=_table_columns(self.store, "replay_daily_basic"))
            return self.store.insert_frame("replay_daily_basic", target), 1
        if source == SourceKind.STK_LIMIT:
            frame = client.stk_limit(trade_date=key)
            rows = []
            for item in frame.to_dict(orient="records"):
                symbol = str(item["ts_code"])
                payload = {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "up_limit": self._value(item.get("up_limit")),
                    "down_limit": self._value(item.get("down_limit")),
                }
                rows.append(
                    [
                        *payload.values(),
                        "Tushare Pro stk_limit",
                        source_timestamp,
                        eod_available,
                        now,
                        _sha(payload),
                    ]
                )
            target = pd.DataFrame(rows, columns=_table_columns(self.store, "replay_stk_limit"))
            return self.store.insert_frame("replay_stk_limit", target), 1
        if source == SourceKind.MARGIN:
            if next_trade_date is None:
                raise RuntimeError("next trading date is unavailable for Margin available_time")
            available = datetime.combine(next_trade_date, MARGIN_AVAILABLE_TIME, tzinfo=SHANGHAI)
            detail = client.margin_detail(trade_date=key)
            market = client.margin(trade_date=key)
            rows = []
            for item in detail.to_dict(orient="records"):
                symbol = str(item["ts_code"])
                payload = {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "margin_balance": self._value(item.get("rzye")),
                    "margin_buy": self._value(item.get("rzmre")),
                    "margin_repay": self._value(item.get("rzche")),
                    "short_balance": self._value(item.get("rqye")),
                    "short_sell": self._value(item.get("rqmcl")),
                    "short_repay": self._value(item.get("rqchl")),
                    "total_margin_balance": self._value(item.get("rzrqye")),
                }
                rows.append(
                    [
                        *payload.values(),
                        "Tushare Pro margin_detail",
                        source_timestamp,
                        available,
                        now,
                        _sha(payload),
                    ]
                )
            target = pd.DataFrame(rows, columns=_table_columns(self.store, "replay_margin"))
            inserted = self.store.insert_frame("replay_margin", target)
            values = [self._value(value) for value in market.get("rzrqye", []).tolist()]
            total = sum(value for value in values if value is not None) if values else None
            payload = {"trade_date": trade_date, "market_margin_total": total}
            market_frame = pd.DataFrame(
                [[trade_date, total, "Tushare Pro margin", source_timestamp, available, now, _sha(payload)]],
                columns=_table_columns(self.store, "replay_margin_market"),
            )
            self.store.insert_frame("replay_margin_market", market_frame)
            return inserted, 2
        if source == SourceKind.DRAGON_TIGER:
            top_list = client.top_list(trade_date=key)
            top_inst = client.top_inst(trade_date=key)
            rows = []
            for symbol, listed in top_list.groupby("ts_code", sort=True):
                first = listed.iloc[0]
                seats = top_inst[top_inst["ts_code"].astype(str) == str(symbol)]
                institutions = seats[
                    seats["exalter"].astype(str).str.contains("机构专用", regex=False, na=False)
                ]
                inst_buy = float(institutions["buy"].fillna(0).sum())
                inst_sell = float(institutions["sell"].fillna(0).sum())
                payload = {
                    "trade_date": trade_date,
                    "symbol": str(symbol),
                    "reasons_json": json.dumps(
                        sorted(set(listed["reason"].dropna().astype(str))),
                        ensure_ascii=False,
                    ),
                    "buy_amount": self._value(first.get("l_buy")),
                    "sell_amount": self._value(first.get("l_sell")),
                    "net_amount": self._value(first.get("net_amount")),
                    "institution_buy_amount": inst_buy,
                    "institution_sell_amount": inst_sell,
                    "institution_net_amount": inst_buy - inst_sell,
                    "seat_count": len(seats),
                    "institution_seat_count": len(institutions),
                }
                rows.append(
                    [
                        *payload.values(),
                        "Tushare Pro top_list/top_inst",
                        source_timestamp,
                        eod_available,
                        now,
                        _sha(payload),
                    ]
                )
            target = pd.DataFrame(
                rows, columns=_table_columns(self.store, "replay_dragon_tiger_events")
            )
            return self.store.insert_frame("replay_dragon_tiger_events", target), 2
        raise ValueError(f"unsupported replay source: {source}")


def _parse_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace("-", "")
    return datetime.strptime(text[:8], "%Y%m%d").date() if text else None


def _table_columns(store: HistoricalReplayStore, table: str) -> list[str]:
    with store.connection() as connection:
        return [row[1] for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()]


def build_availability_manifest(
    store: HistoricalReplayStore,
    *,
    registry_path: str | Path = "trading/research/sentiment/v3/feature_registry.py",
    label_spec_path: str | Path = "Sentiment_V3_Label_Spec_V1.md",
    evaluation_path: str | Path = "Sentiment_V3_Evaluation_Framework_V1.md",
) -> HistoricalReplayAvailabilityManifest:
    date_from, date_to, trading_dates, _, _ = store.raw_range()
    all_dates: list[date]
    with store.connection() as connection:
        store.attach_source(connection)
        all_dates = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT trade_date FROM source.canonical_historical_bars
                WHERE adjustment_type='RAW' AND verification_status<>'CONFLICT'
                ORDER BY trade_date
                """
            ).fetchall()
        ]
    source_state = {
        SourceKind.DAILY_REFERENCE: store.source_date_complete(
            SourceKind.DAILY_REFERENCE, all_dates
        ),
        SourceKind.DAILY_BASIC: store.source_date_complete(SourceKind.DAILY_BASIC, all_dates),
        SourceKind.STK_LIMIT: store.source_date_complete(SourceKind.STK_LIMIT, all_dates),
        SourceKind.MARGIN: store.source_date_complete(SourceKind.MARGIN, all_dates),
        SourceKind.DRAGON_TIGER: store.source_date_complete(SourceKind.DRAGON_TIGER, all_dates),
    }
    rows: list[HistoricalFeatureAvailability] = []
    for definition in FEATURE_REGISTRY_V1.history_safe():
        required_source, required_fields, available_policy = _source_dependencies(definition)
        source_type = definition.source_snapshot_type
        required_kind = None
        permanently_unavailable = (
            definition.feature_name == "market.coverage_ratio"
        )
        if source_type == "MARGIN_FINANCING":
            required_kind = SourceKind.MARGIN
        elif source_type == "DRAGON_TIGER":
            required_kind = SourceKind.DRAGON_TIGER
        elif definition.source_field in {"turnover_rate", "volume_ratio"}:
            required_kind = SourceKind.DAILY_BASIC
        elif definition.source_field in {"is_limit_up", "is_limit_down", "is_broken_board"}:
            required_kind = SourceKind.STK_LIMIT
        elif source_type in {"MARKET", "STOCK"} and definition.source_field not in {
            "relative_market_volume",
            "turnover_change",
            "volume_change",
        }:
            required_kind = SourceKind.DAILY_REFERENCE
        complete = (
            not permanently_unavailable
            and (required_kind is None or source_state[required_kind])
        )
        status = (
            ReplayStatus.FORWARD_ONLY_DUE_TO_SOURCE
            if permanently_unavailable
            else ReplayStatus.REPLAY_READY
            if complete
            else ReplayStatus.REPLAY_SOURCE_BACKFILLABLE
        )
        rows.append(
            HistoricalFeatureAvailability(
                feature_name=definition.feature_name,
                module=definition.module,
                required_source=required_source,
                required_raw_fields=required_fields,
                historical_source_exists=complete,
                historical_date_range=f"{date_from.isoformat()}..{date_to.isoformat()}" if complete else None,
                can_reconstruct_deterministically=True,
                available_time_policy=available_policy,
                pit_safe=True,
                replay_status=status,
                blocking_reason=(
                    None
                    if complete
                    else "historical strong-stock pool is unavailable for frozen meta coverage"
                    if permanently_unavailable
                    else f"{required_kind.value} history not fully backfilled"
                ),
            )
        )
    identity = {
        "manifest_version": AVAILABILITY_MANIFEST_VERSION,
        "registry_content_hash": FEATURE_REGISTRY_V1.content_hash,
        "raw_date_from": date_from,
        "raw_date_to": date_to,
        "features": [item.model_dump(mode="json") for item in rows],
    }
    return HistoricalReplayAvailabilityManifest(
        registry_file_sha256=_file_sha(registry_path),
        registry_content_hash=FEATURE_REGISTRY_V1.content_hash,
        label_spec_sha256=_file_sha(label_spec_path),
        evaluation_sha256=_file_sha(evaluation_path),
        raw_date_from=date_from,
        raw_date_to=date_to,
        raw_trading_dates=trading_dates,
        features=rows,
        content_hash=_sha(identity),
    )


def build_research_set(
    manifest: HistoricalReplayAvailabilityManifest,
) -> HistoricalResearchSet:
    ready = sorted(
        item.feature_name
        for item in manifest.features
        if item.replay_status == ReplayStatus.REPLAY_READY
    )
    excluded = {
        item.feature_name: item.blocking_reason or item.replay_status.value
        for item in manifest.features
        if item.replay_status != ReplayStatus.REPLAY_READY
    }
    identity = {
        "research_set_version": RESEARCH_SET_VERSION,
        "manifest_hash": manifest.content_hash,
        "feature_names": ready,
        "excluded_features": excluded,
    }
    return HistoricalResearchSet(
        manifest_hash=manifest.content_hash,
        feature_names=ready,
        excluded_features=excluded,
        content_hash=_sha(identity),
    )


__all__ = [
    "AVAILABILITY_MANIFEST_VERSION",
    "CUTOFF_POLICY_VERSION",
    "DECISION_CUTOFF_TIME",
    "HistoricalFeatureAvailability",
    "HistoricalReplayAvailabilityManifest",
    "HistoricalReplayStore",
    "HistoricalResearchSet",
    "REPLAY_ALGORITHM_VERSION",
    "REPLAY_DATASET_VERSION",
    "RESEARCH_SET_VERSION",
    "ReplayBuildMetrics",
    "ReplayStatus",
    "SourceKind",
    "TushareReplayBackfill",
    "build_availability_manifest",
    "build_research_set",
    "default_replay_database",
]
