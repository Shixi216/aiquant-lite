"""PIT-aware, observation-only market/subject/anchor snapshots.

Only the pure V3 calculators are reused. No production repository, decision,
scanner, order or position service is imported here.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.research.sentiment.v3.calculators import (
    LimitUpStock,
    MarketQuote,
    SnapshotBuildContext,
    SnapshotInputs,
    calculate_market_snapshot,
    calculate_speculation_snapshot,
)
from trading.research.sentiment.v3.schemas import Finality, QualityFlag, RecordSource


TZ = ZoneInfo("Asia/Shanghai")
VERSION = "market-emotion-observed-v1.0"
POOL_SCOPE = "EASTMONEY_NON_ST_NON_STAR_EXCLUDING_UNBROKEN_NEW_IPO"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[3] / "reports" / "market_emotion"


def digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def number(value: object) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def symbol(value: object) -> str:
    code = str(value).split(".")[0].zfill(6)
    if len(code) != 6 or not code.isdigit():
        raise ValueError("invalid stock code")
    exchange = "BJ" if code.startswith(("4", "8", "920")) else "SH" if code.startswith("6") else "SZ"
    return code + "." + exchange


def pool_rows(source: dict | None, day: date) -> tuple[list[dict] | None, str | None]:
    """An empty pool is a zero only with qdate, tc and raw pool evidence."""
    if not source or source.get("status") not in {"OK", "EMPTY"}:
        return None, "SOURCE_UNAVAILABLE"
    raw = [x.get("response", {}) for x in source.get("provider_responses", [])
           if isinstance(x.get("response", {}).get("data"), dict)
           and "pool" in x["response"]["data"]]
    if len(raw) != 1:
        return None, "MISSING_POOL_DATE_EVIDENCE"
    data = raw[0]["data"]
    if raw[0].get("rc") != 0 or str(data.get("qdate")) != day.strftime("%Y%m%d"):
        return None, "POOL_DATE_MISMATCH"
    rows = source.get("payload", {}).get("rows", [])
    if data.get("tc") != len(data["pool"]) or len(rows) != len(data["pool"]):
        return None, "POOL_PAGINATION_OR_SCHEMA_MISMATCH"
    try:
        codes = [symbol(r["代码"]) for r in rows]
        if len(set(codes)) != len(codes):
            return None, "DUPLICATED_POOL_SYMBOL"
    except (KeyError, ValueError):
        return None, "INVALID_POOL_SYMBOL"
    return rows, None


def normalize_quotes(source: dict | None, day: date, cutoff: datetime):
    if not source or source.get("status") != "OK":
        return None, {}, 0, "CURRENT_QUOTES_UNAVAILABLE"
    rows = source.get("payload", {}).get("rows", [])
    expected = source.get("expected_count", 0)
    valid = {}
    local = cutoff.astimezone(TZ)
    floor = local - timedelta(minutes=10)
    if local.time() >= time(15):
        floor = datetime.combine(day, time(14, 50), tzinfo=TZ)
    elif time(11, 30) <= local.time() < time(13):
        floor = datetime.combine(day, time(11, 20), tzinfo=TZ)
    for row in rows:
        try:
            observed = datetime.strptime(row["source_timestamp"], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
            code = symbol(row["symbol"])
        except (ValueError, KeyError):
            continue
        change, amount = number(row.get("pct_chg")), number(row.get("amount"))
        if (observed.date() != day or observed > cutoff or observed < floor
                or number(row.get("price")) in {None, 0}
                or change is None or amount is None or amount < 0):
            continue
        valid[code] = {**row, "observed": observed}
    if not expected or len(valid) / expected < 0.98:
        return None, valid, expected, "CURRENT_QUOTE_COVERAGE_BELOW_98_PERCENT"
    quotes = [MarketQuote(code, row["pct_chg"] / 100, row["amount"])
              for code, row in valid.items()]
    return quotes, valid, expected, None


def themes(sources: dict, day: date, cutoff: datetime) -> dict:
    # These endpoints are live-only. They must never be relabelled as history.
    if cutoff.astimezone(TZ).date() != day:
        return {"status": "UNAVAILABLE", "reason": "LIVE_CONCEPTS_CANNOT_RECONSTRUCT_HISTORY", "top": []}
    boards = sources.get("ak_concepts", {}).get("payload", {}).get("rows", [])
    flows = sources.get("ak_concept_flow", {}).get("payload", {}).get("rows", [])
    by_name = {}
    duplicates = set()
    for row in flows:
        name = row.get("名称")
        if name in by_name:
            duplicates.add(name)
        by_name[name] = row
    ranked = sorted((r for r in boards if number(r.get("涨跌幅")) is not None),
                    key=lambda r: -r["涨跌幅"])
    top = []
    for row in ranked[:5]:
        name = row["板块名称"]
        flow = by_name.get(name) if name not in duplicates else None
        up, down = number(row.get("上涨家数")), number(row.get("下跌家数"))
        top.append({"board_id": row["板块代码"], "name": name, "classification": "CONCEPT",
                    "change_pct": row["涨跌幅"],
                    "vendor_net_inflow_yuan": number((flow or {}).get("今日主力净流入-净额")),
                    "flow_methodology": "EASTMONEY_VENDOR_DERIVED",
                    "advancers_share_of_movers": up / (up + down) if up is not None and down is not None and up + down else None,
                    "membership_available_at": None, "members": None,
                    "limit_up_count": None, "membership_pit_safe_for_history": False})
    return {"status": "OK" if top else "UNAVAILABLE", "board_count": len(boards),
            "flow_count": len(flows), "ranking": "CONCEPT_RETURN_DESC_V1", "top": top}


def seat_observations(sources: dict) -> dict:
    records = {}
    failures = []
    observed = {}
    for key, result in sources.items():
        if not key.startswith("seat_"):
            continue
        _, side, code = key.split("_", 2)
        if result.get("status") != "OK":
            failures.append(key)
            continue
        observed.setdefault(code, set()).add(side)
        rows = result.get("payload", {}).get("rows", [])
        raw_rows = [row for response in result.get("provider_responses", [])
                    for row in (response.get("response", {}).get("result") or {}).get("data", [])]
        if raw_rows:
            requested_day = result.get("requested_trade_date")
            if any(str(r.get("SECURITY_CODE")) != code or
                   str(r.get("TRADE_DATE", ""))[:10].replace("-", "") != requested_day
                   for r in raw_rows):
                failures.append(key + ":SEAT_DATE_OR_SYMBOL_MISMATCH")
                continue
            rows = [{"交易营业部名称": r.get("OPERATEDEPT_NAME"), "类型": r.get("EXPLANATION"),
                     "买入金额": r.get("BUY"), "卖出金额": r.get("SELL"), "净额": r.get("NET"),
                     "source_seat_id": r.get("OPERATEDEPT_CODE")} for r in raw_rows]
        occurrences = Counter()
        for row in rows:
            name = row.get("交易营业部名称")
            reason = str(row.get("类型") or "UNKNOWN_REPORT_TYPE")
            if not name:
                failures.append(key + ":MISSING_SEAT_NAME")
                continue
            item = {"symbol": symbol(code), "seat_name": name, "report_type": reason,
                    "buy_yuan": number(row.get("买入金额")), "sell_yuan": number(row.get("卖出金额")),
                    "net_yuan": number(row.get("净额")), "trader_identity": None,
                    "available_at": result.get("available_at")}
            seat_id = row.get("source_seat_id")
            anonymous = name == "机构专用" or seat_id in {0, "0"}
            item.update(source_seat_id=seat_id if seat_id not in {0, "0"} else None,
                        seat_identity_ambiguous=anonymous)
            identity = (code, seat_id or name, reason)
            if anonymous:
                # Multiple distinct institutions share label/code 0. Merge only
                # exact economic rows across sides, retaining same-side multiplicity.
                signature = (code, name, reason, item["buy_yuan"], item["sell_yuan"], item["net_yuan"])
                occurrence = occurrences[signature]
                occurrences[signature] += 1
                identity = (*signature, occurrence)
            previous = records.get(identity)
            if previous and any(previous[field] != item[field] for field in ["buy_yuan", "sell_yuan", "net_yuan"]):
                failures.append(key + ":CONFLICTING_SEAT_VALUES")
                # Keep the conflict visible; never silently add both sides twice.
                item.update(buy_yuan=None, sell_yuan=None, net_yuan=None)
            records[identity] = item
    both_sides = all(sides == {"buy", "sell"} for sides in observed.values())
    return {"status": "SAMPLE_COMPLETE" if records and both_sides and not failures else "PARTIAL" if records else "UNAVAILABLE",
            "scope": "FIRST_3_UNIQUE_PUBLISHED_LHB_STOCKS", "whole_market_complete": False,
            "records": list(records.values()), "issues": failures,
            "identity_policy": "RAW_BROKER_SEAT_ONLY_NO_TRADER_ATTRIBUTION"}


def build_snapshot(sources: dict, day: date, previous_day: date | None,
                   cutoff: datetime, *, record_source: RecordSource = RecordSource.REAL) -> dict:
    if cutoff.tzinfo is None:
        raise ValueError("timezone-aware cutoff required")
    for result in sources.values():
        if result.get("available_at") and datetime.fromisoformat(result["available_at"]) > cutoff:
            raise ValueError("provider data unavailable at cutoff")
    issues = []
    pools = {}
    for key in ["ak_limit_up", "ak_limit_down", "ak_broken"]:
        pools[key], error = pool_rows(sources.get(key), day)
        if error:
            issues.append(key + ":" + error)
    up = pools["ak_limit_up"]
    if up is not None and any(number(r.get("连板数")) is None or number(r["连板数"]) < 1 for r in up):
        up = pools["ak_limit_up"] = None
        issues.append("ak_limit_up:INVALID_BOARD_HEIGHT")
    broken = pools["ak_broken"]
    if up is not None and broken is not None:
        if {symbol(r["代码"]) for r in up} & {symbol(r["代码"]) for r in broken}:
            broken = pools["ak_broken"] = None
            issues.append("ak_broken:OVERLAPPING_SEALED_AND_BROKEN_POOLS")
    quotes, quote_map, expected, quote_error = normalize_quotes(sources.get("tencent_quotes"), day, cutoff)
    if quote_error:
        issues.append(quote_error)
    down = pools["ak_limit_down"]
    finality = (Finality.FINAL if cutoff.astimezone(TZ).date() == day
                and cutoff.astimezone(TZ).time() >= time(16) and not issues
                else Finality.PRELIMINARY if cutoff.astimezone(TZ).time() >= time(15)
                else Finality.INTRADAY)
    source_time = max((r["observed"] for r in quote_map.values()), default=cutoff)
    context = SnapshotBuildContext(
        trade_date=day, source="Tencent + AKShare/Eastmoney", source_timestamp=source_time,
        available_time=cutoff, data_cutoff=cutoff, snapshot_time=cutoff,
        finality=finality, record_source=record_source, data_version=VERSION,
        symbol_scope=POOL_SCOPE, quality_flags=(QualityFlag.MULTI_SOURCE,
                                              QualityFlag.SOURCE_TIMESTAMP_INFERRED))
    # Eastmoney historical TopicZTPool can report today's qdate for old data.
    # Use the explicit YesterdayZTPool contract at today's verified qdate instead.
    previous_rows, previous_error = (pool_rows(sources.get("ak_previous_limit_up"), day)
                                     if previous_day else (None, "PREVIOUS_TRADE_DATE_UNAVAILABLE"))
    if previous_rows is not None:
        previous_rows = [{**r, "连板数": r.get("昨日连板数")} for r in previous_rows]
    previous = None
    if previous_rows is not None and all(number(r.get("连板数")) is not None and number(r["连板数"]) >= 1 for r in previous_rows):
        previous_context = SnapshotBuildContext(
            trade_date=previous_day, source="AKShare/Eastmoney historical qdate-verified pool",
            source_timestamp=cutoff, available_time=cutoff, data_cutoff=cutoff, snapshot_time=cutoff,
            finality=Finality.FINAL, record_source=record_source, data_version=VERSION,
            symbol_scope=POOL_SCOPE, quality_flags=(QualityFlag.SOURCE_TIMESTAMP_INFERRED,))
        previous = calculate_speculation_snapshot(previous_context, SnapshotInputs(
            quotes=None, limit_up=[LimitUpStock(symbol(r["代码"]), int(r["连板数"])) for r in previous_rows],
            limit_down_symbols=None, broken_board_symbols=None, strong_stock_symbols=None))
    else:
        issues.append("previous_limit_up:" + (previous_error or "INVALID_BOARD_HEIGHT"))
    inputs = SnapshotInputs(
        quotes=quotes,
        limit_up=None if up is None else [LimitUpStock(symbol(r["代码"]), int(r["连板数"])) for r in up],
        limit_down_symbols=None if down is None else [symbol(r["代码"]) for r in down],
        broken_board_symbols=None if broken is None else [symbol(r["代码"]) for r in broken],
        strong_stock_symbols=None, expected_universe_size=expected, previous_speculation=previous,
        field_provenance={"quotes": "Tencent actual per-symbol timestamp; amount yuan",
                          "pools": "Eastmoney qdate/tc/pool verified"})
    market = calculate_market_snapshot(replace(context, symbol_scope="ALL_A_BREADTH_WITH_EXPLICIT_POOL_SCOPE"), inputs)
    speculation = calculate_speculation_snapshot(context, inputs)
    theme = themes(sources, day, cutoff)
    up_codes = {symbol(r["代码"]) for r in up or []}
    for item in theme["top"]:
        members_source = sources.get("members_" + item["board_id"])
        members = (members_source or {}).get("payload", {}).get("rows", [])
        if members_source and members_source.get("status") == "OK":
            item.update(members=sorted({symbol(r["代码"]) for r in members}),
                        membership_available_at=members_source["available_at"])
            if up is not None:
                item["limit_up_count"] = len(up_codes & set(item["members"]))
    theme_complete = bool(theme["top"]) and all(
        x["members"] is not None and x["vendor_net_inflow_yuan"] is not None
        for x in theme["top"][:3])
    if not theme_complete:
        issues.append("CONCEPT_TOP3_MEMBERS_OR_FLOW_UNAVAILABLE")
    heights = {symbol(r["代码"]): int(r["连板数"]) for r in up or []}
    max_height = max(heights.values(), default=0)
    current_anchors = [code for code, height in heights.items() if height == max_height]
    previous_heights = previous.board_height_by_symbol if previous else {}
    previous_max = max(previous_heights.values(), default=0)
    tracked = []
    for code, height in previous_heights.items():
        if height != previous_max:
            continue
        state = ("UNKNOWN" if up is None or code not in quote_map else "PROMOTED"
                 if heights.get(code, 0) >= height + 1 else "NOT_SEALED_AT_OBSERVATION")
        tracked.append({"symbol": code, "previous_height": height, "current_height": heights.get(code),
                        "state": state, "change_pct": quote_map.get(code, {}).get("pct_chg")})
    source_meta = {}
    for name, result in sources.items():
        source_meta[name] = {k: result.get(k) for k in ["status", "row_count", "available_at",
                              "elapsed_seconds", "error", "raw_path", "requested_trade_date",
                              "reference_trade_date", "expected_count", "universe_version", "request_count"]}
        source_meta[name]["payload_hash"] = digest(result.get("payload"))
        raw_path = Path(result["raw_path"]) if result.get("raw_path") else None
        source_meta[name]["raw_sha256"] = (hashlib.sha256(raw_path.read_bytes()).hexdigest()
                                            if raw_path and raw_path.exists() else None)
    seats = seat_observations(sources)
    vendor_rows = sources.get("zz_sentiment", {}).get("payload")
    if not isinstance(vendor_rows, list) or any(str(r.get("date")) != day.strftime("%Y%m%d") for r in vendor_rows):
        vendor_rows = None
    payload = {"schema_version": VERSION, "trade_date": day.isoformat(),
               "previous_trade_date": previous_day.isoformat() if previous_day else None,
               "available_at": cutoff.isoformat(), "data_cutoff": cutoff.isoformat(),
               "finality": finality.value, "status": "CORE_COMPLETE" if not issues else "PARTIAL",
               "formal_weight": 0, "research_only": True, "creates_trade": False,
               "pool_scope": POOL_SCOPE, "breadth_scope": "ROUTER_ACTIVE_A_SHARE_UNIVERSE",
               "quote_coverage_ratio": len(quote_map) / expected if expected else 0,
               "promotion_cohort_source": "Eastmoney YesterdayZTPool at current qdate; 昨日连板数",
               "issues": issues, "market": market.model_dump(mode="json"),
               "speculation": speculation.model_dump(mode="json"), "themes": theme,
               "anchors": {"current_highest": sorted(current_anchors), "tracked_previous_highest": tracked},
               "cycle": {"phase": "UNKNOWN", "reason": "NO_FROZEN_VALIDATED_CYCLE_RULE",
                         "predictive": False},
               "vendor_sentiment": vendor_rows,
               "seat_status": seats["status"], "seats": seats,
               "layer_status": {"market": "COMPLETE" if not quote_error and all(x is not None for x in pools.values()) else "PARTIAL",
                                "themes": "COMPLETE_TOP3" if theme_complete else "PARTIAL",
                                "anchors": "COMPLETE" if previous is not None and up is not None and quotes is not None else "PARTIAL",
                                "seats": "NOT_IN_CORE_ACCEPTANCE", "cycle": "NOT_VALIDATED"},
               "sources": source_meta}
    content = {k: v for k, v in payload.items() if k not in {"available_at", "data_cutoff", "market", "speculation", "sources"}}
    content["market"] = {k: v for k, v in payload["market"].items() if k != "meta"}
    content["speculation"] = {k: v for k, v in payload["speculation"].items() if k not in {"meta", "previous_snapshot_id"}}
    content["source_payload_hashes"] = {k: v["payload_hash"] for k, v in source_meta.items()}
    payload["snapshot_id"] = "emotion_" + digest(content)[:24]
    return payload


@contextmanager
def collection_lock(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".collect.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if __import__("os").name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if __import__("os").name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def save_snapshot(output: Path, payload: dict) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output / "market_emotion.sqlite3") as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL,
            available_at TEXT NOT NULL, finality TEXT NOT NULL, payload TEXT NOT NULL)""")
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        connection.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?)", (
            payload["snapshot_id"], payload["trade_date"], payload["available_at"], payload["finality"], text))
        existing = connection.execute("SELECT payload FROM snapshots WHERE snapshot_id=?",
                                      (payload["snapshot_id"],)).fetchone()
        result = json.loads(existing[0])
    temporary = output / "latest.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output / "latest.json")
    (output / "latest.md").write_text(format_summary(result), encoding="utf-8")
    with sqlite3.connect(output / "market_emotion.sqlite3") as connection:
        history = [json.loads(r[0]) for r in connection.execute("SELECT payload FROM snapshots")]
    final_days = {p["trade_date"] for p in history
                  if p["finality"] == "FINAL" and p["status"] == "CORE_COMPLETE"}
    progress = {"valid_final_trade_days": len(final_days),
                "review_checkpoints_due": [n for n in [20, 30, 60] if len(final_days) >= n],
                "checkpoints_are_not_profitability_validation": True}
    (output / "collection_progress.json").write_text(json.dumps(progress, indent=2), encoding="utf-8")
    return result


def format_summary(payload: dict) -> str:
    market, speculation = payload["market"], payload["speculation"]
    def display(value):
        return "缺失" if value is None else str(value)
    rate = speculation.get("promotion_rate")
    broken = market.get("broken_board_rate")
    lines = [f"市场情绪观察 | {payload['trade_date']} | {payload['finality']} | {payload['status']}",
             f"涨停 {display(market.get('limit_up_count'))} / 跌停 {display(market.get('limit_down_count'))}"
             f" / 炸板 {display(market.get('broken_board_count'))} / 最高 {display(speculation.get('max_board_height'))} 板",
             "晋级率 " + (f"{rate:.1%}" if rate is not None else "缺失")
             + " / 炸板率 " + (f"{broken:.1%}" if broken is not None else "缺失"),
             "概念涨幅 Top：" + ("、".join(f"{x['name']} {x['change_pct']:+.2f}%" for x in payload['themes']['top'][:3]) or "缺失"),
             "高度锚：" + ("、".join(payload["anchors"]["current_highest"]) or "缺失"),
             "周期：UNKNOWN（规则尚未验证）；仅观察，未进入正式评分。",
             "龙虎榜席位：" + payload.get("seat_status", "UNAVAILABLE") + "（仅营业部，未认定游资身份）",
             "涨跌停池口径：东财非 ST、非科创板；数据可用时间 " + payload["available_at"]]
    if payload["issues"]:
        lines.append("缺失/降级：" + "; ".join(payload["issues"]))
    return "\n".join(lines)


def cached_summary(output: Path = DEFAULT_OUTPUT, *, as_of: datetime | None = None) -> str:
    now = as_of or datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError("timezone-aware decision time required")
    database = output / "market_emotion.sqlite3"
    if not database.exists():
        return "市场情绪观察：尚无已采集快照。"
    # Read only, no schema creation and no production DB access.
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT payload FROM snapshots ORDER BY rowid DESC").fetchall()
    except sqlite3.Error:
        return "市场情绪观察：快照数据库暂不可读。"
    for row in rows:
        payload = json.loads(row[0])
        available = datetime.fromisoformat(payload["available_at"])
        if available <= now and date.fromisoformat(payload["trade_date"]) <= now.astimezone(TZ).date():
            age = (now - available).total_seconds()
            prefix = "上一观察快照（非当前盘中状态）：\n" if age > 600 else ""
            return prefix + format_summary(payload)
    return "市场情绪观察：尚无该时点可用快照。"
