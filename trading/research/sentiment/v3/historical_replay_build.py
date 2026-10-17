from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from trading.research.sentiment.v3.feature_registry import (
    ASSEMBLY_ALGORITHM_VERSION,
    FEATURE_REGISTRY_V1,
    FeatureDataType,
)
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.historical_replay import (
    CUTOFF_POLICY_VERSION,
    REPLAY_ALGORITHM_VERSION,
    REPLAY_DATASET_VERSION,
    HistoricalReplayStore,
    HistoricalResearchSet,
    ReplayBuildMetrics,
)
from trading.research.sentiment.v3.labels import (
    LABEL_ALGORITHM_VERSION,
    LABEL_SCHEMA_VERSION,
    MAE_MFE_ALGORITHM_VERSION,
    PRICE_ADJUSTMENT_VERSION,
    RETURN_ALGORITHM_VERSION,
    LabelKind,
    LabelSpec,
    label_specs_v1,
)


def feature_column(feature_name: str) -> str:
    return feature_name.replace(".", "__")


def _sql_type(data_type: FeatureDataType) -> str:
    return {
        FeatureDataType.INTEGER: "BIGINT",
        FeatureDataType.FLOAT: "DOUBLE",
        FeatureDataType.BOOLEAN: "BOOLEAN",
        FeatureDataType.STRING: "VARCHAR",
        FeatureDataType.JSON: "JSON",
    }[data_type]


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _feature_expression(feature_name: str) -> str:
    mapping = {
        "capital.dragon_tiger.has_dragon_tiger": "(d.symbol IS NOT NULL)",
        "capital.dragon_tiger.institution_net_amount": "d.institution_net_amount",
        "capital.dragon_tiger.institution_seat_count": "d.institution_seat_count",
        "capital.dragon_tiger.net_amount": "d.net_amount",
        "capital.margin.margin_balance": "mg.margin_balance",
        "capital.margin.margin_balance_change": (
            "CASE WHEN mg.margin_balance IS NOT NULL AND pmg.margin_balance IS NOT NULL "
            "THEN mg.margin_balance-pmg.margin_balance END"
        ),
        "capital.margin.margin_balance_change_pct": (
            "CASE WHEN mg.margin_balance IS NOT NULL AND pmg.margin_balance NOT IN (0) "
            "THEN (mg.margin_balance-pmg.margin_balance)/pmg.margin_balance END"
        ),
        "capital.margin.margin_buy": "mg.margin_buy",
        "capital.margin.margin_net_buy": (
            "CASE WHEN mg.margin_buy IS NOT NULL AND mg.margin_repay IS NOT NULL "
            "THEN mg.margin_buy-mg.margin_repay END"
        ),
        "capital.margin.margin_repay": "mg.margin_repay",
        "capital.stock.turnover": "s.amount",
        "market.advance_decline_ratio": "m.advance_decline_ratio",
        "market.advance_ratio": "m.advance_ratio",
        "market.advancers": "m.advancers",
        "market.breadth_strength": "m.breadth_strength",
        "market.decline_ratio": "m.decline_ratio",
        "market.decliners": "m.decliners",
        "market.market_turnover": "m.market_turnover",
        "market.market_turnover_change": "m.market_turnover_change",
        "market.unchanged": "m.unchanged",
        "stock.amplitude": "s.amplitude",
        "stock.close_location_value": "s.close_location_value",
        "stock.gap_and_fade": "s.gap_and_fade",
        "stock.gap_and_follow_through": "s.gap_and_follow_through",
        "stock.high_to_close_drawdown": "s.high_to_close_drawdown",
        "stock.intraday_return_pct": "s.intraday_return_pct",
        "stock.is_broken_board": "s.is_broken_board",
        "stock.is_limit_down": "s.is_limit_down",
        "stock.is_limit_up": "s.is_limit_up",
        "stock.low_to_close_recovery": "s.low_to_close_recovery",
        "stock.open_gap_pct": "s.open_gap_pct",
        "stock.relative_market_return": "s.relative_market_return",
        "stock.relative_market_volume": "s.relative_market_volume",
        "stock.return_pct": "s.return_pct",
        "stock.turnover_change": "s.turnover_change",
        "stock.turnover_rate": "s.turnover_rate",
        "stock.volume_change": "s.volume_change",
        "stock.volume_ratio": "s.volume_ratio",
    }
    if feature_name not in mapping:
        raise ValueError(f"no frozen replay expression for {feature_name}")
    return mapping[feature_name]


class HistoricalReplayMaterializer:
    """Vectorized, single-writer materialization of compact REPLAY research data."""

    def __init__(self, store: HistoricalReplayStore) -> None:
        self.store = store

    def estimate(self, research_set: HistoricalResearchSet) -> dict[str, Any]:
        date_from, date_to, trading_dates, symbols, raw_rows = self.store.raw_range()
        packet_rows = raw_rows
        return {
            "date_from": date_from,
            "date_to": date_to,
            "trading_dates": trading_dates,
            "symbols": symbols,
            "expected_packet_rows": packet_rows,
            "expected_feature_cells": packet_rows * len(research_set.feature_names),
            "expected_logical_label_rows": packet_rows * len(label_specs_v1()),
            "storage_design": "compact wide feature and label tables",
        }

    def build(
        self,
        research_set: HistoricalResearchSet,
        *,
        registry_sha: str,
        label_spec_sha: str,
        evaluation_sha: str,
        chunk_size: int = 10,
        force_dates: Iterable[date] = (),
    ) -> tuple[str, ReplayBuildMetrics]:
        if not research_set.feature_names:
            raise RuntimeError("S3B_RESEARCH_SET_V1 is empty")
        missing = set(research_set.feature_names) - {
            item.feature_name for item in FEATURE_REGISTRY_V1.history_safe()
        }
        if missing:
            raise RuntimeError(f"research set contains non-HISTORY_SAFE features: {sorted(missing)}")
        started = perf_counter()
        with self.store.connection() as connection:
            connection.execute("SET threads=2")
            connection.execute("SET preserve_insertion_order=false")
            self.store.attach_source(connection)
            self._create_support_tables(connection)
            stock_started = perf_counter()
            self._build_stock_base(connection)
            stock_base_seconds = perf_counter() - stock_started
            market_started = perf_counter()
            self._build_market(connection)
            market_seconds = perf_counter() - market_started
            packet_started = perf_counter()
            self._create_packet_table(connection, research_set)
            self._build_packets(
                connection,
                research_set,
                chunk_size=chunk_size,
                force_dates=set(force_dates),
            )
            packet_seconds = perf_counter() - packet_started
            label_started = perf_counter()
            self._build_price_grid(connection)
            self._build_labels(connection, chunk_size=chunk_size)
            label_seconds = perf_counter() - label_started
            dataset_started = perf_counter()
            dataset_id = self._save_dataset(
                connection,
                research_set,
                registry_sha=registry_sha,
                label_spec_sha=label_spec_sha,
                evaluation_sha=evaluation_sha,
            )
            dataset_seconds = perf_counter() - dataset_started
        return dataset_id, ReplayBuildMetrics(
            market_seconds=market_seconds,
            stock_packet_seconds=stock_base_seconds + packet_seconds,
            label_seconds=label_seconds,
            dataset_seconds=dataset_seconds,
            db_write_seconds=perf_counter() - started,
        )

    @staticmethod
    def _create_support_tables(connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_packet_checkpoints (
                trade_date DATE NOT NULL,
                research_set_hash VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                packet_count BIGINT NOT NULL,
                content_hash VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(trade_date, research_set_hash)
            );
            CREATE OR REPLACE TABLE replay_calendar_index AS
            SELECT trade_date,
                   ROW_NUMBER() OVER (ORDER BY trade_date) - 1 AS trade_index,
                   LAG(trade_date) OVER (ORDER BY trade_date) AS previous_trade_date,
                   LEAD(trade_date) OVER (ORDER BY trade_date) AS next_trade_date
            FROM replay_trade_calendar WHERE is_open;
            CREATE OR REPLACE TABLE replay_status_daily AS
            SELECT c.trade_date, h.symbol,
                   BOOL_OR(h.status_type='ST' AND h.status_value) AS is_st,
                   BOOL_OR(h.status_type='SUSPENDED' AND h.status_value) AS is_suspended
            FROM replay_calendar_index c
            JOIN source.historical_security_statuses h
              ON c.trade_date >= h.effective_start
             AND (h.effective_end IS NULL OR c.trade_date <= h.effective_end)
            GROUP BY c.trade_date, h.symbol;
            """
        )

    @staticmethod
    def _build_stock_base(connection) -> None:
        connection.execute(
            """
            CREATE OR REPLACE TABLE replay_stock_base AS
            WITH raw AS (
                SELECT symbol, trade_date, open, high, low, close, volume, amount,
                       primary_source, content_hash AS bar_hash,
                       event_time AS source_timestamp,
                       data_available_time AS available_time
                FROM source.canonical_historical_bars
                WHERE adjustment_type='RAW' AND verification_status<>'CONFLICT'
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol, trade_date
                    ORDER BY generated_at DESC, bar_id
                )=1
            ), factors AS (
                SELECT symbol, trade_date, adj_factor, content_hash AS factor_hash
                FROM source.historical_adjustment_factors
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol, trade_date
                    ORDER BY fetched_at DESC, factor_id
                )=1
            ), joined AS (
                SELECT r.*, f.adj_factor, f.factor_hash,
                       c.trade_index, c.previous_trade_date, c.next_trade_date,
                       ref.previous_close, ref.return_pct,
                       db.turnover_rate, db.volume_ratio,
                       lim.up_limit, lim.down_limit,
                       COALESCE(st.is_st, FALSE) AS is_st,
                       COALESCE(st.is_suspended, FALSE) AS is_suspended,
                       prev.volume AS previous_volume,
                       prev.amount AS previous_amount,
                       sm.list_date, sm.delist_date, sm.exchange
                FROM raw r
                JOIN replay_calendar_index c USING(trade_date)
                JOIN replay_security_master sm USING(symbol)
                LEFT JOIN raw prev
                  ON prev.symbol=r.symbol AND prev.trade_date=c.previous_trade_date
                LEFT JOIN factors f USING(symbol,trade_date)
                LEFT JOIN replay_daily_reference ref USING(symbol,trade_date)
                LEFT JOIN replay_daily_basic db USING(symbol,trade_date)
                LEFT JOIN replay_stk_limit lim USING(symbol,trade_date)
                LEFT JOIN replay_status_daily st USING(symbol,trade_date)
                WHERE (sm.list_date IS NULL OR sm.list_date <= r.trade_date)
                  AND (sm.delist_date IS NULL OR sm.delist_date >= r.trade_date)
                  AND NOT COALESCE(st.is_suspended, FALSE)
            )
            SELECT *,
                CASE WHEN close IS NOT NULL AND previous_close NOT IN (0)
                     THEN COALESCE(return_pct, close/previous_close-1) END AS stock_return,
                CASE WHEN open IS NOT NULL AND previous_close NOT IN (0)
                     THEN open/previous_close-1 END AS open_gap_pct,
                CASE WHEN close IS NOT NULL AND open NOT IN (0)
                     THEN close/open-1 END AS intraday_return_pct,
                CASE WHEN high IS NOT NULL AND low IS NOT NULL AND previous_close NOT IN (0)
                     THEN (high-low)/previous_close END AS amplitude,
                CASE WHEN high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
                           AND high<>low THEN (close-low)/(high-low) END AS close_location_value,
                CASE WHEN high IS NOT NULL AND close IS NOT NULL AND previous_close NOT IN (0)
                     THEN (high-close)/previous_close END AS high_to_close_drawdown,
                CASE WHEN low IS NOT NULL AND close IS NOT NULL AND previous_close NOT IN (0)
                     THEN (close-low)/previous_close END AS low_to_close_recovery,
                CASE WHEN previous_volume NOT IN (0) AND volume IS NOT NULL
                     THEN (volume-previous_volume)/previous_volume END AS volume_change,
                CASE WHEN previous_amount NOT IN (0) AND amount IS NOT NULL
                     THEN (amount-previous_amount)/previous_amount END AS turnover_change,
                CASE WHEN up_limit IS NULL OR close IS NULL THEN NULL
                     ELSE close >= up_limit*(1-0.001) END AS is_limit_up,
                CASE WHEN down_limit IS NULL OR close IS NULL THEN NULL
                     ELSE close <= down_limit*(1+0.001) END AS is_limit_down,
                CASE WHEN up_limit IS NULL OR high IS NULL OR close IS NULL THEN NULL
                     ELSE high >= up_limit*(1-0.001)
                          AND NOT (close >= up_limit*(1-0.001)) END AS is_broken_board
            FROM joined;
            """
        )

    @staticmethod
    def _build_market(connection) -> None:
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE replay_universe_daily AS
            WITH dates AS (
                SELECT DISTINCT trade_date FROM replay_stock_base
            ), base AS (
                SELECT d.trade_date, sm.symbol,
                       COALESCE(st.is_suspended,FALSE) AS is_suspended
                FROM dates d CROSS JOIN replay_security_master sm
                LEFT JOIN replay_status_daily st
                  ON st.trade_date=d.trade_date AND st.symbol=sm.symbol
                WHERE (sm.list_date IS NULL OR sm.list_date<=d.trade_date)
                  AND (sm.delist_date IS NULL OR sm.delist_date>=d.trade_date)
            )
            SELECT trade_date,
                   COUNT(*) AS expected_symbol_count,
                   COUNT(*) FILTER(WHERE NOT is_suspended) AS eligible_symbol_count,
                   SHA256(STRING_AGG(symbol, ',' ORDER BY symbol)
                          FILTER(WHERE NOT is_suspended)) AS symbol_hash
            FROM base GROUP BY trade_date;

            CREATE OR REPLACE TABLE replay_market_snapshots AS
            WITH daily AS (
                SELECT s.trade_date,
                       COUNT(stock_return) AS valid_quote_count,
                       COUNT(*) FILTER(WHERE stock_return>0) AS advancers,
                       COUNT(*) FILTER(WHERE stock_return<0) AS decliners,
                       COUNT(*) FILTER(WHERE stock_return=0) AS unchanged,
                       AVG(stock_return) AS market_return,
                       SUM(amount) AS market_turnover,
                       SUM(volume) AS market_volume,
                       COUNT(previous_volume) AS previous_volume_count,
                       COUNT(*) AS observed_symbol_count,
                       SUM(previous_volume) AS previous_market_volume,
                       MAX(source_timestamp) AS source_timestamp,
                       MAX(available_time) AS available_time
                FROM replay_stock_base s GROUP BY s.trade_date
            ), enriched AS (
                SELECT d.*, u.expected_symbol_count, u.eligible_symbol_count, u.symbol_hash,
                       LAG(d.market_turnover) OVER(ORDER BY d.trade_date) AS previous_market_turnover
                FROM daily d JOIN replay_universe_daily u USING(trade_date)
            )
            SELECT *,
                   CASE WHEN valid_quote_count>0 THEN advancers::DOUBLE/valid_quote_count END
                       AS advance_ratio,
                   CASE WHEN valid_quote_count>0 THEN decliners::DOUBLE/valid_quote_count END
                       AS decline_ratio,
                   CASE WHEN decliners>0 THEN advancers::DOUBLE/decliners END
                       AS advance_decline_ratio,
                   CASE WHEN valid_quote_count>0
                        THEN (advancers-decliners)::DOUBLE/valid_quote_count END
                       AS breadth_strength,
                   CASE WHEN previous_market_turnover NOT IN (0)
                        THEN (market_turnover-previous_market_turnover)
                             /previous_market_turnover END AS market_turnover_change,
                   CASE WHEN eligible_symbol_count>0
                        THEN valid_quote_count::DOUBLE/eligible_symbol_count END
                       AS quote_coverage_ratio,
                   CASE WHEN previous_volume_count=observed_symbol_count
                             AND previous_market_volume NOT IN (0)
                        THEN (market_volume-previous_market_volume)/previous_market_volume END
                       AS market_volume_change,
                   'sv3_replay_universe_'||SUBSTR(SHA256(
                       trade_date::VARCHAR||'|'||symbol_hash||'|A_SHARE_UNIVERSE_RULES_V1'
                   ),1,32) AS universe_id,
                   'A_SHARE_BASE_V1_'||SUBSTR(SHA256(
                       trade_date::VARCHAR||'|'||symbol_hash||'|A_SHARE_UNIVERSE_RULES_V1'
                   ),1,16) AS universe_version,
                   'sv3_replay_market_'||SUBSTR(SHA256(
                       trade_date::VARCHAR||'|'||COALESCE(valid_quote_count::VARCHAR,'NULL')||'|'||
                       COALESCE(market_turnover::VARCHAR,'NULL')||'|'||
                       COALESCE(advancers::VARCHAR,'NULL')||'|'||COALESCE(decliners::VARCHAR,'NULL')
                   ),1,32) AS market_snapshot_id,
                   available_time AS data_cutoff, CURRENT_TIMESTAMP AS snapshot_time,
                   'FINAL' AS finality, 'REPLAY' AS record_source,
                   'SENTIMENT_V3_HISTORICAL_REPLAY_SCHEMA_V1' AS schema_version,
                   {_sql_string(REPLAY_ALGORITHM_VERSION)} AS algorithm_version,
                   SHA256(CONCAT_WS('|',trade_date::VARCHAR,valid_quote_count::VARCHAR,
                       market_turnover::VARCHAR,advancers::VARCHAR,decliners::VARCHAR))
                       AS content_hash,
                   CURRENT_TIMESTAMP AS created_at
            FROM enriched;

            CREATE OR REPLACE TABLE replay_stock_snapshots AS
            SELECT s.*,
                   m.market_return,
                   m.market_volume_change,
                   CASE WHEN s.stock_return IS NOT NULL AND m.market_return IS NOT NULL
                        THEN s.stock_return-m.market_return END AS relative_market_return,
                   CASE WHEN s.volume_change IS NOT NULL AND m.market_volume_change IS NOT NULL
                        THEN s.volume_change-m.market_volume_change END AS relative_market_volume,
                   CASE WHEN s.open_gap_pct IS NULL OR s.intraday_return_pct IS NULL THEN NULL
                        WHEN s.open_gap_pct=0 THEN 0.0
                        WHEN SIGN(s.open_gap_pct)*s.intraday_return_pct<0
                        THEN ABS(s.intraday_return_pct) ELSE 0.0 END AS gap_and_fade,
                   CASE WHEN s.open_gap_pct IS NULL OR s.intraday_return_pct IS NULL THEN NULL
                        WHEN s.open_gap_pct=0 THEN 0.0
                        WHEN SIGN(s.open_gap_pct)*s.intraday_return_pct>0
                        THEN ABS(s.intraday_return_pct) ELSE 0.0 END AS gap_and_follow_through,
                   m.universe_id, m.universe_version, m.market_snapshot_id,
                   s.available_time AS data_cutoff, CURRENT_TIMESTAMP AS snapshot_time,
                   'FINAL' AS finality, 'REPLAY' AS record_source,
                   'SENTIMENT_V3_HISTORICAL_REPLAY_SCHEMA_V1' AS schema_version,
                   {_sql_string(REPLAY_ALGORITHM_VERSION)} AS algorithm_version,
                   'sv3_replay_stock_'||SUBSTR(SHA256(
                       s.trade_date::VARCHAR||'|'||s.symbol||'|'||s.bar_hash||'|'||
                       COALESCE(s.factor_hash,'NO_FACTOR')||'|'||
                       COALESCE(s.return_pct::VARCHAR,'NULL')
                   ),1,32) AS stock_snapshot_id,
                   SHA256(CONCAT_WS('|',s.trade_date::VARCHAR,s.symbol,s.bar_hash,
                       COALESCE(s.factor_hash,'NO_FACTOR'),
                       COALESCE(s.return_pct::VARCHAR,'NULL'))) AS content_hash,
                   CURRENT_TIMESTAMP AS created_at
            FROM replay_stock_base s JOIN replay_market_snapshots m USING(trade_date);
            """
        )

    @staticmethod
    def _create_packet_table(connection, research_set: HistoricalResearchSet) -> None:
        definitions = {
            item.feature_name: item for item in FEATURE_REGISTRY_V1.history_safe()
        }
        feature_columns = ",\n".join(
            f"{_quoted(feature_column(name))} {_sql_type(definitions[name].data_type)}"
            for name in research_set.feature_names
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS replay_feature_packets (
                packet_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                feature_trade_date DATE NOT NULL,
                decision_cutoff TIMESTAMPTZ NOT NULL,
                cutoff_policy_version VARCHAR NOT NULL,
                record_source VARCHAR NOT NULL,
                registry_version VARCHAR NOT NULL,
                registry_content_hash VARCHAR NOT NULL,
                research_set_hash VARCHAR NOT NULL,
                universe_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                market_snapshot_id VARCHAR NOT NULL,
                stock_snapshot_id VARCHAR NOT NULL,
                margin_snapshot_id VARCHAR NOT NULL,
                dragon_tiger_snapshot_id VARCHAR NOT NULL,
                market_available_time TIMESTAMPTZ NOT NULL,
                stock_available_time TIMESTAMPTZ NOT NULL,
                margin_available_time TIMESTAMPTZ NOT NULL,
                dragon_tiger_available_time TIMESTAMPTZ NOT NULL,
                quality_status VARCHAR NOT NULL,
                quality_flags_json JSON NOT NULL,
                feature_lineage_hash VARCHAR NOT NULL,
                assembly_algorithm_version VARCHAR NOT NULL,
                content_hash VARCHAR NOT NULL,
                {feature_columns},
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    def _build_packets(
        self,
        connection,
        research_set: HistoricalResearchSet,
        *,
        chunk_size: int,
        force_dates: set[date],
    ) -> None:
        dates = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT trade_date FROM replay_stock_snapshots ORDER BY trade_date"
            ).fetchall()
        ]
        complete = {
            row[0]
            for row in connection.execute(
                """
                SELECT trade_date FROM replay_packet_checkpoints
                WHERE research_set_hash=? AND status='COMPLETE'
                """,
                [research_set.content_hash],
            ).fetchall()
        }
        pending = [day for day in dates if day not in complete or day in force_dates]
        for start in range(0, len(pending), max(1, chunk_size)):
            chunk = pending[start : start + max(1, chunk_size)]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            if force_dates.intersection(chunk):
                connection.execute(
                    f"DELETE FROM replay_feature_packets WHERE trade_date IN ({placeholders}) "
                    "AND research_set_hash=?",
                    [*chunk, research_set.content_hash],
                )
            self._insert_packet_chunk(connection, research_set, chunk)
            now = datetime.now().astimezone()
            rows = []
            for day in chunk:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM replay_feature_packets
                    WHERE trade_date=? AND research_set_hash=?
                    """,
                    [day, research_set.content_hash],
                ).fetchone()[0]
                rows.append(
                    [
                        day,
                        research_set.content_hash,
                        "COMPLETE",
                        count,
                        _sha_sql_payload(day, research_set.content_hash, count),
                        now,
                    ]
                )
            frame = __import__("pandas").DataFrame(
                rows,
                columns=[
                    "trade_date",
                    "research_set_hash",
                    "status",
                    "packet_count",
                    "content_hash",
                    "updated_at",
                ],
            )
            connection.register("_packet_checkpoints", frame)
            connection.execute(
                """
                INSERT OR REPLACE INTO replay_packet_checkpoints
                SELECT * FROM _packet_checkpoints
                """
            )
            connection.unregister("_packet_checkpoints")
            print(
                f"replay packet assembly {min(start+len(chunk),len(pending))}/{len(pending)} dates",
                flush=True,
            )

    @staticmethod
    def _insert_packet_chunk(
        connection, research_set: HistoricalResearchSet, dates: list[date]
    ) -> None:
        placeholders = ",".join("?" for _ in dates)
        feature_select = ",\n".join(
            f"{_feature_expression(name)} AS {_quoted(feature_column(name))}"
            for name in research_set.feature_names
        )
        feature_hash_values = ",".join(
            f"COALESCE(CAST({_feature_expression(name)} AS VARCHAR),'NULL')"
            for name in research_set.feature_names
        )
        generic_required = [
            name
            for name in research_set.feature_names
            if not name.startswith("capital.margin.")
            and not name.startswith("capital.dragon_tiger.")
        ]
        missing_count = "+".join(
            f"CASE WHEN {_feature_expression(name)} IS NULL THEN 1 ELSE 0 END"
            for name in generic_required
        ) or "0"
        connection.execute(
            f"""
            INSERT INTO replay_feature_packets
            SELECT
                'sv3_replay_packet_'||SUBSTR(SHA256(
                    s.symbol||'|'||s.trade_date::VARCHAR||'|'||
                    {_sql_string(CUTOFF_POLICY_VERSION)}||'|'||
                    {_sql_string(research_set.content_hash)}
                ),1,32) AS packet_id,
                s.symbol, s.trade_date, s.trade_date,
                (s.next_trade_date::DATE + INTERVAL '9 hours 10 minutes') AT TIME ZONE 'Asia/Shanghai' AS decision_cutoff,
                {_sql_string(CUTOFF_POLICY_VERSION)}, 'REPLAY',
                {_sql_string(FEATURE_REGISTRY_V1.registry_version)},
                {_sql_string(FEATURE_REGISTRY_V1.content_hash)},
                {_sql_string(research_set.content_hash)},
                s.universe_id, s.universe_version,
                s.market_snapshot_id, s.stock_snapshot_id,
                'sv3_replay_margin_'||SUBSTR(SHA256(
                    s.symbol||'|'||s.trade_date::VARCHAR||'|MARGIN'
                ),1,32),
                'sv3_replay_dragon_'||SUBSTR(SHA256(
                    s.symbol||'|'||s.trade_date::VARCHAR||'|DRAGON_TIGER'
                ),1,32),
                m.available_time,
                s.available_time,
                COALESCE(mg.available_time,
                    (s.next_trade_date::DATE + INTERVAL '9 hours') AT TIME ZONE 'Asia/Shanghai'),
                (s.trade_date::DATE + INTERVAL '18 hours') AT TIME ZONE 'Asia/Shanghai',
                CASE WHEN ({missing_count})>0 OR m.quote_coverage_ratio<1.0
                     THEN 'PARTIAL' ELSE 'COMPLETE' END,
                CAST('['||CONCAT_WS(',',
                    CASE WHEN ({missing_count})>0 THEN '"PARTIAL_DATA"' END,
                    CASE WHEN m.quote_coverage_ratio<1.0 THEN '"LOW_COVERAGE"' END,
                    CASE WHEN mg.symbol IS NULL THEN '"NO_MARGIN_ELIGIBILITY"' END,
                    CASE WHEN d.symbol IS NULL THEN '"NO_DRAGON_TIGER"' END
                )||']' AS JSON),
                SHA256(CONCAT_WS('|',s.market_snapshot_id,s.stock_snapshot_id,
                    COALESCE(mg.content_hash,'NO_MARGIN_ELIGIBILITY'),
                    COALESCE(d.content_hash,'NO_DRAGON_TIGER'),
                    {_sql_string(REPLAY_ALGORITHM_VERSION)}
                )),
                {_sql_string(ASSEMBLY_ALGORITHM_VERSION)},
                SHA256(CONCAT_WS('|',s.symbol,s.trade_date::VARCHAR,
                    {_sql_string(research_set.content_hash)},{feature_hash_values},
                    CASE WHEN ({missing_count})>0 OR m.quote_coverage_ratio<1.0
                         THEN 'PARTIAL' ELSE 'COMPLETE' END,
                    CAST('['||CONCAT_WS(',',
                        CASE WHEN ({missing_count})>0 THEN '"PARTIAL_DATA"' END,
                        CASE WHEN m.quote_coverage_ratio<1.0 THEN '"LOW_COVERAGE"' END,
                        CASE WHEN mg.symbol IS NULL THEN '"NO_MARGIN_ELIGIBILITY"' END,
                        CASE WHEN d.symbol IS NULL THEN '"NO_DRAGON_TIGER"' END
                    )||']' AS VARCHAR))),
                {feature_select},
                CURRENT_TIMESTAMP
            FROM replay_stock_snapshots s
            JOIN replay_market_snapshots m USING(trade_date)
            JOIN replay_calendar_index c USING(trade_date)
            LEFT JOIN replay_margin mg USING(trade_date,symbol)
            LEFT JOIN replay_margin pmg
              ON pmg.symbol=s.symbol AND pmg.trade_date=c.previous_trade_date
            LEFT JOIN replay_dragon_tiger_events d USING(trade_date,symbol)
            WHERE s.trade_date IN ({placeholders}) AND s.next_trade_date IS NOT NULL
            ON CONFLICT(packet_id) DO NOTHING
            """,
            dates,
        )

    @staticmethod
    def _build_price_grid(connection) -> None:
        frames = [1, 2, 3, 5, 10, 20]
        windows = ",\n".join(
            [
                f"MIN(adjusted_low) OVER(PARTITION BY symbol ORDER BY trade_index "
                f"ROWS BETWEEN 1 FOLLOWING AND {offset} FOLLOWING) AS future_min_{offset}",
                f"MAX(adjusted_high) OVER(PARTITION BY symbol ORDER BY trade_index "
                f"ROWS BETWEEN 1 FOLLOWING AND {offset} FOLLOWING) AS future_max_{offset}",
                f"COUNT(close) OVER(PARTITION BY symbol ORDER BY trade_index "
                f"ROWS BETWEEN 1 FOLLOWING AND {offset} FOLLOWING) AS future_observed_{offset}",
            ][item]
            for offset in frames
            for item in range(3)
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE replay_price_bars AS
            WITH raw AS (
                SELECT symbol,trade_date,open,high,low,close,volume,amount,content_hash
                FROM source.canonical_historical_bars
                WHERE adjustment_type='RAW' AND verification_status<>'CONFLICT'
                QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol,trade_date
                    ORDER BY generated_at DESC,bar_id)=1
            ), factors AS (
                SELECT symbol,trade_date,adj_factor,content_hash AS factor_hash
                FROM source.historical_adjustment_factors
                QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol,trade_date
                    ORDER BY fetched_at DESC,factor_id)=1
            )
            SELECT r.*,f.adj_factor,f.factor_hash,ref.previous_close,
                   COALESCE(st.is_st,FALSE) AS is_st,
                   COALESCE(st.is_suspended,FALSE) AS is_suspended
            FROM raw r LEFT JOIN factors f USING(symbol,trade_date)
            LEFT JOIN replay_daily_reference ref USING(symbol,trade_date)
            LEFT JOIN replay_status_daily st USING(symbol,trade_date);

            CREATE OR REPLACE TABLE replay_price_grid AS
            WITH symbols AS (SELECT DISTINCT symbol FROM replay_feature_packets),
            grid AS (
                SELECT s.symbol,c.trade_date,c.trade_index,
                       p.open,p.high,p.low,p.close,p.volume,p.amount,p.previous_close,
                       p.adj_factor,p.is_st,p.is_suspended,
                       CASE WHEN sm.delist_date IS NOT NULL AND c.trade_date>=sm.delist_date
                            THEN TRUE ELSE FALSE END AS is_delisted,
                       p.content_hash,p.factor_hash,
                       CASE WHEN p.low>0 AND p.adj_factor>0 THEN p.low*p.adj_factor END
                           AS adjusted_low,
                       CASE WHEN p.high>0 AND p.adj_factor>0 THEN p.high*p.adj_factor END
                           AS adjusted_high
                FROM symbols s CROSS JOIN replay_calendar_index c
                LEFT JOIN replay_price_bars p USING(symbol,trade_date)
                LEFT JOIN replay_security_master sm ON sm.symbol=s.symbol
            )
            SELECT *,{windows} FROM grid;
            """
        )

    @staticmethod
    def _build_labels(connection, *, chunk_size: int = 10) -> None:
        specs = label_specs_v1()
        joins: list[str] = []
        columns: list[str] = []
        for spec in specs:
            prefix = _label_prefix(spec)
            entry_offset = 0 if spec.label_kind == LabelKind.STATISTICAL else 1
            exit_offset = spec.horizon if spec.label_kind == LabelKind.STATISTICAL else max(
                spec.horizon, 2
            )
            entry = f"e_{prefix}"
            exit_ = f"x_{prefix}"
            joins.extend(
                [
                    f"LEFT JOIN replay_price_grid {entry} ON {entry}.symbol=p.symbol "
                    f"AND {entry}.trade_index=sg.trade_index+{entry_offset}",
                    f"LEFT JOIN replay_price_grid {exit_} ON {exit_}.symbol=p.symbol "
                    f"AND {exit_}.trade_index=sg.trade_index+{exit_offset}",
                ]
            )
            entry_price = f"{entry}.close" if entry_offset == 0 else f"{entry}.open"
            adjusted_entry = f"({entry_price}*{entry}.adj_factor)"
            adjusted_exit = f"({exit_}.close*{exit_}.adj_factor)"
            insufficient = (
                f"{exit_}.trade_date IS NULL OR {exit_}.trade_date>"
                "(SELECT MAX(trade_date) FROM replay_price_bars WHERE content_hash IS NOT NULL)"
            )
            entry_absent = f"{entry}.content_hash IS NULL"
            entry_missing = f"{entry_price} IS NULL OR {entry_price}<=0"
            exit_absent = f"{exit_}.content_hash IS NULL"
            exit_missing = f"{exit_}.close IS NULL OR {exit_}.close<=0"
            entry_delisted = f"({entry_missing}) AND {entry}.is_delisted"
            exit_delisted = f"({exit_missing}) AND {exit_}.is_delisted"
            entry_suspended_missing = f"({entry_missing}) AND {entry}.is_suspended"
            exit_suspended_missing = f"({exit_missing}) AND {exit_}.is_suspended"
            entry_suspended = f"{entry}.is_suspended OR {entry}.volume=0"
            exit_suspended = f"{exit_}.is_suspended OR {exit_}.volume=0"
            rate_entry = _limit_rate_sql(entry)
            rate_exit = _limit_rate_sql(exit_)
            one_entry = (
                f"{entry}.open IS NOT NULL AND {entry}.high IS NOT NULL AND "
                f"{entry}.low IS NOT NULL AND {entry}.close IS NOT NULL AND "
                f"GREATEST({entry}.open,{entry}.high,{entry}.low,{entry}.close)-"
                f"LEAST({entry}.open,{entry}.high,{entry}.low,{entry}.close)<=1e-9"
            )
            one_exit = (
                f"{exit_}.open IS NOT NULL AND {exit_}.high IS NOT NULL AND "
                f"{exit_}.low IS NOT NULL AND {exit_}.close IS NOT NULL AND "
                f"GREATEST({exit_}.open,{exit_}.high,{exit_}.low,{exit_}.close)-"
                f"LEAST({exit_}.open,{exit_}.high,{exit_}.low,{exit_}.close)<=1e-9"
            )
            locked_entry = (
                f"({one_entry}) AND {entry_price}>={entry}.previous_close*"
                f"(1+{rate_entry})*(1-0.001)"
            )
            locked_exit = (
                f"({one_exit}) AND {exit_}.close<={exit_}.previous_close*"
                f"(1-{rate_exit})*(1+0.001)"
            )
            no_adjustment = (
                f"{entry}.adj_factor IS NULL OR {entry}.adj_factor<=0 OR "
                f"{exit_}.adj_factor IS NULL OR {exit_}.adj_factor<=0"
            )
            invalid_order = [
                (insufficient, "INSUFFICIENT_FORWARD_WINDOW"),
                (entry_absent, "MISSING_PRICE"),
                (entry_delisted, "DELISTED"),
                (entry_suspended_missing, "SUSPENDED_ENTRY"),
                (entry_missing, "MISSING_PRICE"),
            ]
            if spec.execution_realistic:
                invalid_order.extend(
                    [
                        (entry_suspended, "SUSPENDED_ENTRY"),
                        (locked_entry, "LIMIT_UP_LOCKED_ENTRY"),
                    ]
                )
            invalid_order.extend(
                [
                    (exit_absent, "MISSING_PRICE"),
                    (exit_delisted, "DELISTED"),
                    (exit_suspended_missing, "SUSPENDED_EXIT"),
                    (exit_missing, "MISSING_PRICE"),
                ]
            )
            if spec.execution_realistic:
                invalid_order.extend(
                    [
                        (exit_suspended, "SUSPENDED_EXIT"),
                        (locked_exit, "LIMIT_DOWN_LOCKED_EXIT"),
                    ]
                )
            invalid_order.append((no_adjustment, "ADJUSTMENT_UNAVAILABLE"))
            reason_case = "CASE " + " ".join(
                f"WHEN {condition} THEN '{reason}'" for condition, reason in invalid_order
            ) + " END"
            invalid_condition = " OR ".join(
                f"({condition})" for condition, _ in invalid_order
            )
            observed = f"sg.future_observed_{exit_offset}"
            columns.extend(
                [
                    f"{entry}.trade_date AS {prefix}__entry_date",
                    f"{exit_}.trade_date AS {prefix}__exit_date",
                    f"CASE WHEN {invalid_condition} THEN NULL ELSE "
                    f"{exit_}.close/{entry_price}-1 END AS {prefix}__raw_return",
                    f"CASE WHEN {invalid_condition} THEN NULL ELSE "
                    f"{adjusted_exit}/{adjusted_entry}-1 END AS {prefix}__adjusted_return",
                    f"CASE WHEN {invalid_condition} THEN NULL ELSE "
                    f"sg.future_min_{exit_offset}/{adjusted_entry}-1 END AS {prefix}__mae",
                    f"CASE WHEN {invalid_condition} THEN NULL ELSE "
                    f"sg.future_max_{exit_offset}/{adjusted_entry}-1 END AS {prefix}__mfe",
                    f"CASE WHEN {insufficient} THEN 'INSUFFICIENT_FORWARD_WINDOW' "
                    f"WHEN {invalid_condition} THEN 'INVALID' "
                    f"WHEN {observed}<{exit_offset} THEN 'PARTIAL' ELSE 'COMPLETE' END "
                    f"AS {prefix}__status",
                    f"{reason_case} AS {prefix}__missing_reason",
                    f"{_sql_string(spec.spec_hash)} AS {prefix}__spec_hash",
                ]
            )
        set_hash = _label_spec_set_hash(specs)
        hash_fields = [
            f"{_label_prefix(spec)}__{field}"
            for spec in specs
            for field in (
                "entry_date", "exit_date", "raw_return", "adjusted_return",
                "mae", "mfe", "status", "missing_reason", "spec_hash",
            )
        ]
        output_hash_values = ",".join(
            f"COALESCE(CAST({_quoted(field)} AS VARCHAR),'NULL')" for field in hash_fields
        )

        def select_sql(where_clause: str) -> str:
            return f"""
                WITH label_values AS (
                    SELECT p.packet_id,p.symbol,p.trade_date,p.decision_cutoff,
                           p.record_source,
                           {_sql_string(LABEL_SCHEMA_VERSION)} AS label_schema_version,
                           {_sql_string(LABEL_ALGORITHM_VERSION)} AS label_algorithm_version,
                           {_sql_string(RETURN_ALGORITHM_VERSION)} AS return_algorithm_version,
                           {_sql_string(MAE_MFE_ALGORITHM_VERSION)} AS mae_mfe_algorithm_version,
                           {_sql_string(PRICE_ADJUSTMENT_VERSION)} AS price_adjustment_method,
                           {_sql_string(set_hash)} AS label_spec_set_hash,
                           {','.join(columns)}
                    FROM replay_feature_packets p
                    JOIN replay_price_grid sg
                      ON sg.symbol=p.symbol AND sg.trade_date=p.trade_date
                    {' '.join(joins)}
                    {where_clause}
                )
                SELECT *,SHA256(CONCAT_WS('|',packet_id,label_spec_set_hash,
                           {_sql_string(REPLAY_ALGORITHM_VERSION)},
                           {output_hash_values})) AS content_hash,
                       CURRENT_TIMESTAMP AS created_at
                FROM label_values
            """

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_label_checkpoints (
                trade_date DATE PRIMARY KEY,
                status VARCHAR NOT NULL,
                label_row_count BIGINT NOT NULL,
                content_hash VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS replay_labels_wide AS " + select_sql("WHERE FALSE")
        )
        dates = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT trade_date FROM replay_feature_packets ORDER BY trade_date"
            ).fetchall()
        ]
        complete = {
            row[0]
            for row in connection.execute(
                "SELECT trade_date FROM replay_label_checkpoints WHERE status='COMPLETE'"
            ).fetchall()
        }
        pending = [day for day in dates if day not in complete]
        for start in range(0, len(pending), max(1, chunk_size)):
            chunk = pending[start : start + max(1, chunk_size)]
            placeholders = ",".join("?" for _ in chunk)
            connection.execute(
                f"DELETE FROM replay_labels_wide WHERE trade_date IN ({placeholders})", chunk
            )
            connection.execute(
                "INSERT INTO replay_labels_wide "
                + select_sql(f"WHERE p.trade_date IN ({placeholders})"),
                chunk,
            )
            now = datetime.now().astimezone()
            rows = []
            for day in chunk:
                count = connection.execute(
                    "SELECT COUNT(*) FROM replay_labels_wide WHERE trade_date=?", [day]
                ).fetchone()[0]
                rows.append([day, "COMPLETE", count, _sha_sql_payload(day, set_hash, count), now])
            frame = __import__("pandas").DataFrame(
                rows,
                columns=["trade_date", "status", "label_row_count", "content_hash", "updated_at"],
            )
            connection.register("_label_checkpoints", frame)
            connection.execute(
                "INSERT OR REPLACE INTO replay_label_checkpoints SELECT * FROM _label_checkpoints"
            )
            connection.unregister("_label_checkpoints")
            print(
                f"replay label assembly {min(start+len(chunk),len(pending))}/{len(pending)} dates",
                flush=True,
            )

    @staticmethod
    def _save_dataset(
        connection,
        research_set: HistoricalResearchSet,
        *,
        registry_sha: str,
        label_spec_sha: str,
        evaluation_sha: str,
    ) -> str:
        stats = connection.execute(
            """
            SELECT MIN(trade_date),MAX(trade_date),COUNT(DISTINCT trade_date),
                   COUNT(*),COUNT(DISTINCT symbol),
                   BIT_XOR(HASH(content_hash))
            FROM replay_feature_packets WHERE research_set_hash=?
            """,
            [research_set.content_hash],
        ).fetchone()
        date_from, date_to, dates, packets, symbols, packet_hash_xor = stats
        label_hash_xor = connection.execute(
            "SELECT BIT_XOR(HASH(content_hash)) FROM replay_labels_wide",
        ).fetchone()[0]
        payload = {
            "dataset_version": REPLAY_DATASET_VERSION,
            "registry_sha": registry_sha,
            "registry_content_hash": FEATURE_REGISTRY_V1.content_hash,
            "label_spec_sha": label_spec_sha,
            "evaluation_sha": evaluation_sha,
            "cutoff_policy_version": CUTOFF_POLICY_VERSION,
            "date_from": date_from,
            "date_to": date_to,
            "trading_date_count": dates,
            "packet_count": packets,
            "symbol_count": symbols,
            "research_feature_set": research_set.model_dump(mode="json"),
            "feature_count": len(research_set.feature_names),
            "label_specs": [item.model_dump(mode="json") for item in label_specs_v1()],
            "replay_algorithm_version": REPLAY_ALGORITHM_VERSION,
            "record_source": "REPLAY",
            "packet_hash_xor": packet_hash_xor,
            "label_hash_xor": label_hash_xor,
        }
        content_hash = _hash_payload(payload)
        dataset_id = f"sv3_s3b_replay_{content_hash[:32]}"
        now = datetime.now().astimezone()
        connection.execute(
            """
            INSERT INTO replay_dataset_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(dataset_id) DO NOTHING
            """,
            [
                dataset_id,
                REPLAY_DATASET_VERSION,
                registry_sha,
                label_spec_sha,
                evaluation_sha,
                CUTOFF_POLICY_VERSION,
                date_from,
                date_to,
                dates,
                packets,
                symbols,
                len(research_set.feature_names),
                "REPLAY",
                content_hash,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                now,
            ],
        )
        return dataset_id


def _label_prefix(spec: LabelSpec) -> str:
    kind = "stat" if spec.label_kind == LabelKind.STATISTICAL else "exec"
    return f"{kind}_{spec.horizon}d"


def _limit_rate_sql(alias: str) -> str:
    return (
        f"CASE WHEN {alias}.is_st THEN 0.05 "
        f"WHEN {alias}.symbol LIKE '%.BJ' THEN 0.30 "
        f"WHEN LEFT({alias}.symbol,3) IN ('688','689') THEN 0.20 "
        f"WHEN LEFT({alias}.symbol,3) IN ('300','301') "
        f"AND {alias}.trade_date>=DATE '2020-08-24' THEN 0.20 ELSE 0.10 END"
    )


def _label_spec_set_hash(specs: Iterable[LabelSpec]) -> str:
    return _hash_payload([item.model_dump(mode="json") for item in specs])


def _hash_payload(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sha_sql_payload(day: date, research_set_hash: str, count: int) -> str:
    return _hash_payload(
        {"trade_date": day, "research_set_hash": research_set_hash, "packet_count": count}
    )


def replay_database_size(path: str | Path) -> int:
    return Path(path).stat().st_size if Path(path).exists() else 0


__all__ = [
    "HistoricalReplayMaterializer",
    "feature_column",
    "replay_database_size",
]
