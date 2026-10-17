from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pytest

from trading.research.sentiment.v3.feature_registry import FEATURE_REGISTRY_V1
from trading.research.sentiment.v3.historical_replay import (
    CUTOFF_POLICY_VERSION,
    DECISION_CUTOFF_TIME,
    EXECUTABLE_ENTRY_TIME,
    HistoricalReplayStore,
    ReplayStatus,
    SourceKind,
    build_availability_manifest,
    build_research_set,
)
from trading.research.sentiment.v3.historical_replay_build import (
    HistoricalReplayMaterializer,
)
from trading.research.sentiment.v3.labels import label_specs_v1


TZ = ZoneInfo("Asia/Shanghai")
START = date(2026, 6, 1)
DAYS = tuple(START + timedelta(days=index) for index in range(30))
CALENDAR_DAYS = tuple(START + timedelta(days=index) for index in range(35))
SYMBOLS = ("600000.SH", "000001.SZ", "430001.BJ")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _create_source(path: Path) -> None:
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE canonical_historical_bars (
            bar_id VARCHAR, symbol VARCHAR, trade_date DATE, open DOUBLE,
            high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, amount DOUBLE,
            primary_source VARCHAR, content_hash VARCHAR, event_time TIMESTAMPTZ,
            data_available_time TIMESTAMPTZ, adjustment_type VARCHAR,
            verification_status VARCHAR, generated_at TIMESTAMPTZ
        );
        CREATE TABLE historical_adjustment_factors (
            factor_id VARCHAR, symbol VARCHAR, trade_date DATE, adj_factor DOUBLE,
            content_hash VARCHAR, fetched_at TIMESTAMPTZ
        );
        CREATE TABLE historical_security_statuses (
            symbol VARCHAR, status_type VARCHAR, status_value BOOLEAN,
            effective_start DATE, effective_end DATE
        );
        """
    )
    now = datetime.now(tz=TZ)
    bars = []
    factors = []
    for day_index, day in enumerate(DAYS):
        for symbol_index, symbol in enumerate(SYMBOLS):
            previous = 10.0 + symbol_index + day_index * 0.03
            close = previous * (1 + (symbol_index - 1) * 0.002)
            bars.append(
                (
                    f"bar-{symbol}-{day}", symbol, day, previous, previous * 1.02,
                    previous * 0.98, close, 1000 + day_index, 10000 + day_index,
                    "TEST", _sha(f"bar-{symbol}-{day}"),
                    datetime.combine(day, time(15), tzinfo=TZ),
                    datetime.combine(day, time(18), tzinfo=TZ), "RAW", "VERIFIED", now,
                )
            )
            factors.append(
                (f"factor-{symbol}-{day}", symbol, day, 1.0, _sha(f"factor-{symbol}-{day}"), now)
            )
    connection.executemany(
        "INSERT INTO canonical_historical_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        bars,
    )
    connection.executemany(
        "INSERT INTO historical_adjustment_factors VALUES (?,?,?,?,?,?)", factors
    )
    connection.close()


def _populate_replay(store: HistoricalReplayStore) -> None:
    now = datetime.now(tz=TZ)
    with store.connection() as connection:
        for symbol in SYMBOLS:
            exchange = symbol.split(".")[-1]
            connection.execute(
                "INSERT INTO replay_security_master VALUES (?,?,?,?,?,?,?,?,?,?)",
                [symbol, symbol, "TEST", exchange, "L", START, None, "TEST", now, _sha(symbol)],
            )
        for day in CALENDAR_DAYS:
            connection.execute(
                "INSERT INTO replay_trade_calendar VALUES (?,true,'TEST',?,?)",
                [day, now, _sha(str(day))],
            )
        for day in DAYS:
            for symbol_index, symbol in enumerate(SYMBOLS):
                previous = 10.0 + symbol_index + (day - START).days * 0.03
                available = datetime.combine(day, time(18), tzinfo=TZ)
                source_time = datetime.combine(day, time(15), tzinfo=TZ)
                payload = _sha(f"{symbol}-{day}")
                connection.execute(
                    "INSERT INTO replay_daily_reference VALUES (?,?,?,?,?,?,?,?,?)",
                    [day, symbol, previous, (symbol_index - 1) * 0.002, "TEST", source_time, available, now, payload],
                )
                connection.execute(
                    "INSERT INTO replay_daily_basic VALUES (?,?,?,?,?,?,?,?,?)",
                    [day, symbol, 0.1, 1.0, "TEST", source_time, available, now, payload],
                )
                connection.execute(
                    "INSERT INTO replay_stk_limit VALUES (?,?,?,?,?,?,?,?,?)",
                    [day, symbol, previous * 1.1, previous * 0.9, "TEST", source_time, available, now, payload],
                )
                if symbol != "430001.BJ":
                    next_day = day + timedelta(days=1)
                    connection.execute(
                        "INSERT INTO replay_margin VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [day, symbol, 1000.0, 20.0, 10.0, 5.0, 1.0, 0.5, 1005.0,
                         "TEST", source_time, datetime.combine(next_day, time(9), tzinfo=TZ), now, payload],
                    )
        for source in (
            SourceKind.DAILY_REFERENCE, SourceKind.DAILY_BASIC, SourceKind.STK_LIMIT,
            SourceKind.MARGIN, SourceKind.DRAGON_TIGER,
        ):
            for day in DAYS:
                connection.execute(
                    "INSERT INTO replay_source_checkpoints VALUES (?,?, 'COMPLETE',0,0,NULL,NULL,?,?)",
                    [source.value, day, _sha(f"{source}-{day}"), now],
                )


@pytest.fixture
def replay(tmp_path: Path) -> tuple[HistoricalReplayStore, Path]:
    source = tmp_path / "source.duckdb"
    replay_path = tmp_path / "replay.duckdb"
    _create_source(source)
    store = HistoricalReplayStore(replay_path, source)
    _populate_replay(store)
    return store, replay_path


def _manifest(store: HistoricalReplayStore):
    return build_availability_manifest(store)


def test_01_history_safe_audit_covers_all_39_canonical_features(replay):
    store, _ = replay
    manifest = _manifest(store)
    assert len(FEATURE_REGISTRY_V1.history_safe()) == 39
    assert len(manifest.features) == 39
    assert len({item.feature_name for item in manifest.features}) == 39


def test_02_research_set_excludes_only_non_replay_ready_features(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    assert len(research_set.feature_names) == 38
    assert research_set.excluded_features == {
        "market.coverage_ratio": "historical strong-stock pool is unavailable for frozen meta coverage"
    }
    assert all("sector." not in name for name in research_set.feature_names)


def test_03_cutoff_policy_is_preopen_and_strictly_before_entry():
    assert CUTOFF_POLICY_VERSION == "PREOPEN_REPLAY_CUTOFF_V1"
    assert DECISION_CUTOFF_TIME == time(9, 10)
    assert DECISION_CUTOFF_TIME < EXECUTABLE_ENTRY_TIME


def test_04_manifest_and_research_set_are_deterministic(replay):
    store, _ = replay
    first = _manifest(store)
    second = _manifest(store)
    assert first.content_hash == second.content_hash
    assert build_research_set(first).content_hash == build_research_set(second).content_hash


def test_05_source_checkpoint_is_resume_and_idempotency_boundary(replay):
    store, _ = replay
    assert store.source_date_complete(SourceKind.MARGIN, DAYS)
    assert store.source_date_complete(SourceKind.DRAGON_TIGER, DAYS)
    frame = pd.DataFrame(
        [[DAYS[0], SYMBOLS[0], 1.0, 1.0, "TEST", datetime.now(tz=TZ),
          datetime.now(tz=TZ), datetime.now(tz=TZ), "same"]],
        columns=["trade_date", "symbol", "up_limit", "down_limit", "source",
                 "source_timestamp", "available_time", "ingested_at", "content_hash"],
    )
    assert store.insert_frame("replay_stk_limit", frame) == 1
    assert store.insert_frame("replay_stk_limit", frame) == 1
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM replay_stk_limit WHERE trade_date=? AND symbol=?",
            [DAYS[0], SYMBOLS[0]],
        ).fetchone()[0] == 1


def test_06_vectorized_multidate_replay_build_and_lineage(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    dataset_id, _ = HistoricalReplayMaterializer(store).build(
        research_set,
        registry_sha="registry",
        label_spec_sha="labels",
        evaluation_sha="evaluation",
        chunk_size=7,
    )
    with store.connection() as connection:
        packets = connection.execute("SELECT COUNT(*) FROM replay_feature_packets").fetchone()[0]
        dates = connection.execute(
            "SELECT COUNT(DISTINCT trade_date) FROM replay_feature_packets"
        ).fetchone()[0]
        sources = connection.execute(
            "SELECT DISTINCT record_source FROM replay_feature_packets"
        ).fetchall()
        assert packets == len(SYMBOLS) * len(DAYS)
        assert dates == len(DAYS)
        assert sources == [("REPLAY",)]
        assert connection.execute(
            "SELECT COUNT(*) FROM replay_dataset_metadata WHERE dataset_id=?", [dataset_id]
        ).fetchone()[0] == 1


def test_07_same_date_market_feature_is_identical_for_all_symbols(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    HistoricalReplayMaterializer(store).build(
        research_set, registry_sha="r", label_spec_sha="l", evaluation_sha="e"
    )
    with store.connection() as connection:
        maximum = connection.execute(
            "SELECT MAX(n) FROM (SELECT trade_date,COUNT(DISTINCT market__advancers) n "
            "FROM replay_feature_packets GROUP BY trade_date)"
        ).fetchone()[0]
    assert maximum == 1


def test_08_packet_pit_and_t_plus_one_market_exclusion(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    HistoricalReplayMaterializer(store).build(
        research_set, registry_sha="r", label_spec_sha="l", evaluation_sha="e"
    )
    with store.connection() as connection:
        leaks = connection.execute(
            """
            SELECT COUNT(*) FROM replay_feature_packets
            WHERE market_available_time>decision_cutoff
               OR stock_available_time>decision_cutoff
               OR margin_available_time>decision_cutoff
               OR dragon_tiger_available_time>decision_cutoff
               OR date_part('hour',decision_cutoff AT TIME ZONE 'Asia/Shanghai')>=10
               OR (date_part('hour',decision_cutoff AT TIME ZONE 'Asia/Shanghai')=9
                   AND date_part('minute',decision_cutoff AT TIME ZONE 'Asia/Shanghai')>=30)
            """
        ).fetchone()[0]
    assert leaks == 0


def test_09_margin_noneligibility_is_missing_not_zero(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    HistoricalReplayMaterializer(store).build(
        research_set, registry_sha="r", label_spec_sha="l", evaluation_sha="e"
    )
    with store.connection() as connection:
        row = connection.execute(
            "SELECT capital__margin__margin_balance,quality_flags_json "
            "FROM replay_feature_packets WHERE symbol='430001.BJ' LIMIT 1"
        ).fetchone()
    assert row[0] is None
    assert "NO_MARGIN_ELIGIBILITY" in row[1]


def test_10_no_dragon_tiger_is_false_not_provider_failure(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    HistoricalReplayMaterializer(store).build(
        research_set, registry_sha="r", label_spec_sha="l", evaluation_sha="e"
    )
    with store.connection() as connection:
        values = connection.execute(
            "SELECT DISTINCT capital__dragon_tiger__has_dragon_tiger "
            "FROM replay_feature_packets"
        ).fetchall()
    assert values == [(False,)]


def test_11_all_ten_frozen_label_specs_are_materialized(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    HistoricalReplayMaterializer(store).build(
        research_set, registry_sha="r", label_spec_sha="l", evaluation_sha="e"
    )
    with store.connection() as connection:
        names = {row[1] for row in connection.execute("PRAGMA table_info('replay_labels_wide')").fetchall()}
    assert len(label_specs_v1()) == 10
    assert {"stat_1d__status", "stat_20d__status", "exec_1d__status", "exec_20d__status"} <= names


def test_12_right_edge_is_not_filled_with_zero(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    HistoricalReplayMaterializer(store).build(
        research_set, registry_sha="r", label_spec_sha="l", evaluation_sha="e"
    )
    with store.connection() as connection:
        row = connection.execute(
            "SELECT stat_20d__status,stat_20d__adjusted_return FROM replay_labels_wide "
            "ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    assert row == ("INSUFFICIENT_FORWARD_WINDOW", None)


def test_13_replay_build_is_idempotent(replay):
    store, _ = replay
    research_set = build_research_set(_manifest(store))
    builder = HistoricalReplayMaterializer(store)
    first, _ = builder.build(
        research_set, registry_sha="r", label_spec_sha="l", evaluation_sha="e"
    )
    with store.connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*),MIN(content_hash),MAX(content_hash) FROM replay_feature_packets"
        ).fetchone()
    second, _ = builder.build(
        research_set, registry_sha="r", label_spec_sha="l", evaluation_sha="e"
    )
    with store.connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*),MIN(content_hash),MAX(content_hash) FROM replay_feature_packets"
        ).fetchone()
    assert first == second
    assert before == after


def test_14_registry_formal_weights_remain_zero():
    assert all(item.formal_weight == 0 for item in FEATURE_REGISTRY_V1.definitions)



def test_15_manifest_distinguishes_backfillable_from_forward_only(tmp_path: Path):
    source = tmp_path / "source-only.duckdb"
    _create_source(source)
    store = HistoricalReplayStore(tmp_path / "empty-replay.duckdb", source)
    manifest = _manifest(store)
    counts = {}
    for item in manifest.features:
        counts[item.replay_status.value] = counts.get(item.replay_status.value, 0) + 1
    assert counts == {
        "REPLAY_READY": 4,
        "REPLAY_SOURCE_BACKFILLABLE": 34,
        "FORWARD_ONLY_DUE_TO_SOURCE": 1,
    }


def test_16_research_set_never_admits_non_ready_features(replay):
    store, _ = replay
    manifest = _manifest(store)
    research_set = build_research_set(manifest)
    status = {item.feature_name: item.replay_status for item in manifest.features}
    assert all(status[name] == ReplayStatus.REPLAY_READY for name in research_set.feature_names)
