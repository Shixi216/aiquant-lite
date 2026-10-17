from __future__ import annotations

import ast
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from trading.research.sentiment.v3.schemas import RecordSource
from trading.research.market_emotion.service import (
    TZ,
    build_snapshot,
    cached_summary,
    collection_lock,
    normalize_quotes,
    pool_rows,
    save_snapshot,
    seat_observations,
)


DAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 16, 10, 30, tzinfo=TZ)


def pool(rows, *, day=DAY, key="getTopicZTPool"):
    return {"status": "OK" if rows else "EMPTY", "available_at": NOW.isoformat(),
            "payload": {"rows": rows}, "provider_responses": [{"endpoint": key,
                "response": {"rc": 0, "data": {"qdate": int(day.strftime("%Y%m%d")),
                    "tc": len(rows), "pool": [dict(r) for r in rows]}}}]}


def sources():
    return {
        "ak_limit_up": pool([{"代码": "000001", "连板数": 3}, {"代码": "600001", "连板数": 1}]),
        "ak_limit_down": pool([]),
        "ak_broken": pool([{"代码": "000002"}]),
        "ak_previous_limit_up": pool([{"代码": "000001", "昨日连板数": 2},
                                      {"代码": "000002", "昨日连板数": 1}], key="getYesterdayZTPool"),
        "tencent_quotes": {"status": "OK", "expected_count": 3, "available_at": NOW.isoformat(),
                           "payload": {"rows": [
                               {"symbol": "000001.SZ", "pct_chg": 10, "amount": 100000,
                                "price": 10, "source_timestamp": "20261016103000"},
                               {"symbol": "000002.SZ", "pct_chg": 2, "amount": 200000,
                                "price": 20, "source_timestamp": "20261016103000"},
                               {"symbol": "600001.SH", "pct_chg": 10, "amount": 300000,
                                "price": 30, "source_timestamp": "20261016103000"},
                           ]}},
        "ak_concepts": {"status": "OK", "payload": {"rows": [
            {"板块名称": "题材甲", "板块代码": "BK1", "涨跌幅": 3, "上涨家数": 2, "下跌家数": 1}]}},
        "ak_concept_flow": {"status": "OK", "payload": {"rows": [
            {"名称": "题材甲", "今日主力净流入-净额": 90000}]}},
        "members_BK1": {"status": "OK", "available_at": NOW.isoformat(),
                        "payload": {"rows": [{"代码": "000001"}, {"代码": "600001"}]}}}


def build(data=None, *, cutoff=NOW):
    return build_snapshot(data or sources(), DAY, date(2026, 10, 15), cutoff,
                          record_source=RecordSource.TEST)


def test_empty_pool_is_zero_only_with_source_date_and_total():
    assert pool_rows(pool([]), DAY) == ([], None)
    value = pool([])
    value["provider_responses"] = []
    assert pool_rows(value, DAY)[0] is None


@pytest.mark.parametrize("field,value", [("qdate", 20261015), ("tc", 999)])
def test_wrong_date_or_partial_pagination_is_rejected(field, value):
    source = pool([{"代码": "000001"}])
    source["provider_responses"][0]["response"]["data"][field] = value
    assert pool_rows(source, DAY)[0] is None


def test_duplicate_stock_does_not_inflate_counts():
    assert pool_rows(pool([{"代码": "000001"}, {"代码": "000001"}]), DAY)[0] is None


def test_failure_is_missing_instead_of_zero():
    data = sources()
    data["ak_limit_down"] = {"status": "TIMEOUT"}
    result = build(data)
    assert result["market"]["limit_down_count"] is None
    assert result["status"] == "PARTIAL"


def test_v3_rates_and_units_are_reused():
    result = build()
    assert result["status"] == "CORE_COMPLETE"
    assert result["market"]["limit_down_count"] == 0
    assert result["market"]["market_turnover"] == 600000
    assert result["market"]["broken_board_rate"] == pytest.approx(1 / 3)
    assert result["speculation"]["promotion_denominator"] == 2
    assert result["speculation"]["promotion_rate"] == 0.5


def test_yesterday_cohort_uses_explicit_yesterday_height():
    result = build()
    anchor = result["anchors"]["tracked_previous_highest"][0]
    assert anchor["symbol"] == "000001.SZ"
    assert anchor["state"] == "PROMOTED"
    assert result["anchors"]["current_highest"] == ["000001.SZ"]


def test_historical_pool_with_today_qdate_is_not_relabeled():
    data = sources()
    data["ak_previous_limit_up"] = pool([{"代码": "000001", "连板数": 2}])
    result = build(data)
    assert result["speculation"]["promotion_rate"] is None


def test_sealed_and_broken_overlap_is_not_double_counted():
    data = sources()
    data["ak_broken"] = pool([{"代码": "000001"}])
    result = build(data)
    assert result["market"]["broken_board_rate"] is None
    assert any("OVERLAPPING" in issue for issue in result["issues"])


def test_prior_day_quotes_cannot_be_called_current():
    source = sources()["tencent_quotes"]
    for row in source["payload"]["rows"]:
        row["source_timestamp"] = "20261014150000"
    assert normalize_quotes(source, DAY, NOW)[0] is None


def test_same_day_stale_quotes_cannot_be_called_live():
    source = sources()["tencent_quotes"]
    for row in source["payload"]["rows"]:
        row["source_timestamp"] = "20261016093000"
    assert normalize_quotes(source, DAY, NOW)[0] is None


def test_final_requires_after_close_and_fresh_close_quotes():
    assert build()["finality"] == "INTRADAY"
    data = sources()
    close = datetime(2026, 10, 16, 16, 10, tzinfo=TZ)
    for row in data["tencent_quotes"]["payload"]["rows"]:
        row["source_timestamp"] = "20261016150000"
    assert build(data, cutoff=close)["finality"] == "FINAL"
    assert build(sources(), cutoff=close)["status"] == "PARTIAL"


def test_coverage_uses_full_universe_denominator():
    source = sources()["tencent_quotes"]
    source["expected_count"] = 100
    assert normalize_quotes(source, DAY, NOW)[0] is None


def test_future_data_is_rejected_at_provider_boundary():
    data = sources()
    data["ak_limit_up"]["available_at"] = (NOW + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="unavailable at cutoff"):
        build(data)


def test_future_quote_timestamp_cannot_increase_coverage():
    source = sources()["tencent_quotes"]
    source["payload"]["rows"][0]["source_timestamp"] = "20261016103100"
    assert normalize_quotes(source, DAY, NOW)[0] is None


def test_current_membership_is_observed_not_backfilled():
    result = build()
    theme = result["themes"]["top"][0]
    assert theme["limit_up_count"] == 2
    assert theme["classification"] == "CONCEPT"
    assert theme["membership_pit_safe_for_history"] is False
    assert theme["membership_available_at"] == NOW.isoformat()


def test_highest_anchor_preserves_ties():
    data = sources()
    data["ak_limit_up"]["payload"]["rows"][1]["连板数"] = 3
    assert build(data)["anchors"]["current_highest"] == ["000001.SZ", "600001.SH"]


def test_exact_repeat_is_idempotent_and_history_is_append_only(tmp_path):
    first = build()
    assert save_snapshot(tmp_path, first)["snapshot_id"] == first["snapshot_id"]
    assert save_snapshot(tmp_path, first)["snapshot_id"] == first["snapshot_id"]
    second = deepcopy(first)
    second.update(snapshot_id="emotion_second", available_at=(NOW + timedelta(minutes=1)).isoformat())
    save_snapshot(tmp_path, second)
    import sqlite3

    with sqlite3.connect(tmp_path / "market_emotion.sqlite3") as c:
        assert c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
        assert c.execute("SELECT payload FROM snapshots WHERE snapshot_id=?", (first["snapshot_id"],)).fetchone()


def test_report_does_not_read_a_future_snapshot(tmp_path):
    save_snapshot(tmp_path, build())
    assert "尚无该时点" in cached_summary(tmp_path, as_of=NOW - timedelta(seconds=1))
    assert "上一观察快照" in cached_summary(tmp_path, as_of=NOW + timedelta(minutes=11))


def test_reader_does_not_create_a_database(tmp_path):
    assert "尚无已采集" in cached_summary(tmp_path, as_of=NOW)
    assert not list(tmp_path.iterdir())


def test_corrupt_observation_database_cannot_break_existing_report(tmp_path):
    database = tmp_path / "market_emotion.sqlite3"
    database.write_bytes(b"not a database")
    assert "暂不可读" in cached_summary(tmp_path, as_of=NOW)
    assert database.read_bytes() == b"not a database"


def test_collector_lock_rejects_concurrent_writer(tmp_path):
    with collection_lock(tmp_path):
        with pytest.raises(OSError):
            with collection_lock(tmp_path):
                pass


def test_observation_has_no_action_weight_or_order_path():
    result = build()
    assert result["formal_weight"] == 0
    assert result["research_only"] is True
    assert result["creates_trade"] is False
    assert result["market"]["meta"]["record_source"] == "TEST"
    assert result["cycle"]["phase"] == "UNKNOWN"
    service = Path(__file__).parents[1] / "trading/research/market_emotion/service.py"
    imports = [n.module or "" for n in ast.walk(ast.parse(service.read_text(encoding="utf-8")))
               if isinstance(n, ast.ImportFrom)]
    assert not any("decision_support" in n or "scanner" in n or "database" in n for n in imports)


def test_seat_buy_sell_overlap_is_deduplicated_without_identity_claim():
    row = {"交易营业部名称": "某证券某营业部", "类型": "单日", "买入金额": 100,
           "卖出金额": 50, "净额": 50}
    data = {f"seat_{side}_000001": {"status": "OK", "payload": {"rows": [row]}}
            for side in ["buy", "sell"]}
    result = seat_observations(data)
    assert result["status"] == "SAMPLE_COMPLETE"
    assert len(result["records"]) == 1
    assert result["records"][0]["net_yuan"] == 50
    assert result["records"][0]["trader_identity"] is None
    assert result["whole_market_complete"] is False


def test_seat_conflicting_values_are_not_summed():
    data = {}
    for side, amount in [("buy", 50), ("sell", 100)]:
        data[f"seat_{side}_000001"] = {"status": "OK", "payload": {"rows": [
            {"交易营业部名称": "营业部", "类型": "单日", "净额": amount}]}}
    result = seat_observations(data)
    assert result["status"] == "PARTIAL"
    assert result["records"][0]["net_yuan"] is None


def test_anonymous_institutions_are_not_collapsed_by_shared_name():
    rows = [{"交易营业部名称": "机构专用", "类型": "单日", "净额": amount} for amount in [50, 100]]
    data = {f"seat_{side}_000001": {"status": "OK", "payload": {"rows": rows}}
            for side in ["buy", "sell"]}
    result = seat_observations(data)
    assert result["status"] == "SAMPLE_COMPLETE"
    assert len(result["records"]) == 2
    assert all(r["seat_identity_ambiguous"] for r in result["records"])


def test_identical_anonymous_rows_within_one_side_keep_multiplicity():
    row = {"交易营业部名称": "机构专用", "类型": "单日", "净额": 50}
    data = {f"seat_{side}_000001": {"status": "OK", "payload": {"rows": [row, row]}}
            for side in ["buy", "sell"]}
    assert len(seat_observations(data)["records"]) == 2
