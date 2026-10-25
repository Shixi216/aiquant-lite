"""Read-only market, sector and stock context from the Router-owned database."""
from __future__ import annotations

import json
from typing import Any

from data_hub.services.full_market_common import normalize_symbol


def _table_exists(connection: Any, name: str) -> bool:
    return bool(connection.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()[0])


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _regime(advance_ratio: float | None, median_change: float | None) -> str:
    if advance_ratio is None or median_change is None:
        return "UNKNOWN"
    if advance_ratio >= 0.60 and median_change >= 0.5:
        return "RISK_ON"
    if advance_ratio <= 0.40 and median_change <= -0.5:
        return "RISK_OFF"
    return "BALANCED"


class EquityContextReader:
    """Aggregate persisted facts; never fetch data or create decisions."""

    def overview(self, *, sector_limit: int = 100) -> dict[str, Any]:
        from database.db import get_connection

        with get_connection(read_only=True) as connection:
            snapshot = connection.execute("""
                SELECT snapshot_id, snapshot_time, coverage_ratio, completeness_status
                FROM market_snapshot_runs
                WHERE mode = 'APPLY' AND completeness_status != 'FAILED'
                ORDER BY snapshot_time DESC LIMIT 1
            """).fetchone()
            if snapshot is None:
                raise KeyError("no market snapshot")
            breadth = connection.execute("""
                SELECT COUNT(*) FILTER (WHERE change_pct > 0),
                       COUNT(*) FILTER (WHERE change_pct < 0),
                       COUNT(*) FILTER (WHERE change_pct = 0),
                       median(change_pct), SUM(amount * CASE
                           WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'CNY' THEN 1
                           WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'THOUSAND_CNY' THEN 1000
                           WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'TEN_THOUSAND_CNY' THEN 10000
                           WHEN source = 'Tushare' THEN 1000
                           WHEN source = 'Tencent' THEN 10000 ELSE 1 END)
                FROM market_snapshot_items WHERE snapshot_id = ?
                  AND change_pct IS NOT NULL
            """, [snapshot[0]]).fetchone()
            valid = sum(int(value or 0) for value in breadth[:3])
            advance_ratio = (float(breadth[0]) / valid) if valid else None
            sectors: list[dict[str, Any]] = []
            if _table_exists(connection, "stock_industry_memberships"):
                rows = connection.execute("""
                    WITH industry AS (
                        SELECT symbol, industry_name, classification_system,
                               ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY
                                   CASE WHEN valid_to IS NULL THEN 0 ELSE 1 END,
                                   generated_at DESC) AS rn
                        FROM stock_industry_memberships
                    ), scores AS (
                        SELECT symbol, composite_score FROM cross_section_factor_snapshot
                    )
                    SELECT i.industry_name, i.classification_system, COUNT(*),
                           COUNT(*) FILTER (WHERE m.change_pct > 0),
                           COUNT(*) FILTER (WHERE m.change_pct < 0),
                           median(m.change_pct), SUM(m.amount * CASE
                               WHEN COALESCE(TRY(json_extract_string(m.payload_json, '$.amount_unit')), '') = 'CNY' THEN 1
                               WHEN COALESCE(TRY(json_extract_string(m.payload_json, '$.amount_unit')), '') = 'THOUSAND_CNY' THEN 1000
                               WHEN COALESCE(TRY(json_extract_string(m.payload_json, '$.amount_unit')), '') = 'TEN_THOUSAND_CNY' THEN 10000
                               WHEN m.source = 'Tushare' THEN 1000
                               WHEN m.source = 'Tencent' THEN 10000 ELSE 1 END),
                           AVG(s.composite_score)
                    FROM market_snapshot_items m
                    JOIN industry i ON i.symbol = m.symbol AND i.rn = 1
                    LEFT JOIN scores s ON s.symbol = m.symbol
                    WHERE m.snapshot_id = ? AND m.change_pct IS NOT NULL
                    GROUP BY i.industry_name, i.classification_system
                    ORDER BY median(m.change_pct) DESC NULLS LAST, COUNT(*) DESC
                    LIMIT ?
                """, [snapshot[0], sector_limit]).fetchall()
                sectors = [{
                    "sector": row[0], "classification_system": row[1],
                    "member_count": int(row[2]), "advances": int(row[3]),
                    "declines": int(row[4]),
                    "advance_ratio": float(row[3]) / max(1, int(row[2])),
                    "median_change_pct": None if row[5] is None else float(row[5]),
                    "total_amount": None if row[6] is None else float(row[6]),
                    "average_composite_score": None if row[7] is None else float(row[7]),
                } for row in rows]
            macro: dict[str, Any] = {
                "market_regime": _regime(advance_ratio, breadth[3]),
                "advance_ratio": advance_ratio,
                "median_change_pct": None if breadth[3] is None else float(breadth[3]),
                "total_amount": None if breadth[4] is None else float(breadth[4]),
            }
            if _table_exists(connection, "sentiment_market_snapshots"):
                row = connection.execute("""
                    SELECT score, confidence, temperature, data_cutoff,
                           algorithm_version, risk_flags_json
                    FROM sentiment_market_snapshots
                    ORDER BY data_cutoff DESC, generated_at DESC LIMIT 1
                """).fetchone()
                if row:
                    macro["sentiment"] = {
                        "score": float(row[0]), "confidence": float(row[1]),
                        "temperature": row[2], "data_cutoff": row[3],
                        "algorithm_version": row[4], "risk_flags": _json(row[5]) or [],
                        "formal_status": "shadow",
                    }
            if _table_exists(connection, "capital_flow_market_snapshots"):
                row = connection.execute("""
                    SELECT score, confidence, data_cutoff, algorithm_version
                    FROM capital_flow_market_snapshots
                    ORDER BY data_cutoff DESC, generated_at DESC LIMIT 1
                """).fetchone()
                if row:
                    macro["capital_flow"] = {
                        "score": float(row[0]), "confidence": float(row[1]),
                        "data_cutoff": row[2], "algorithm_version": row[3],
                        "formal_status": "shadow",
                    }
        return {
            "snapshot_id": snapshot[0], "data_cutoff": snapshot[1],
            "coverage_ratio": float(snapshot[2]), "completeness_status": snapshot[3],
            "valid_symbol_count": valid, "macro": macro, "sectors": sectors,
            "calculation": "sector_snapshot_aggregate_v1", "source": "router_main_database",
        }

    def search_stocks(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Search persisted stock identities by code, short name or company name."""
        from database.db import get_connection

        keyword = query.strip()
        if not keyword:
            return []
        upper = keyword.upper()
        contains = f"%{keyword}%"
        code_prefix = f"{upper}%"
        candidates: list[dict[str, Any]] = []
        with get_connection(read_only=True) as connection:
            if _table_exists(connection, "stock_universe"):
                rows = connection.execute("""
                    SELECT symbol, COALESCE(short_name, company_name), company_name,
                           exchange, board, listing_status, verification_status, primary_source
                    FROM stock_universe
                    WHERE UPPER(symbol) LIKE ?
                       OR short_name ILIKE ? OR company_name ILIKE ?
                    ORDER BY CASE
                        WHEN UPPER(symbol) = ? OR split_part(UPPER(symbol), '.', 1) = ? THEN 0
                        WHEN short_name = ? THEN 1
                        WHEN short_name ILIKE ? THEN 2
                        WHEN UPPER(symbol) LIKE ? THEN 3
                        ELSE 4 END,
                        CASE WHEN listing_status = 'ACTIVE' THEN 0 ELSE 1 END,
                        symbol
                    LIMIT ?
                """, [code_prefix, contains, contains, upper, upper, keyword,
                        f"{keyword}%", code_prefix, limit * 3]).fetchall()
                candidates.extend({
                    "symbol": row[0], "name": row[1], "company_name": row[2],
                    "exchange": row[3], "board": row[4], "listing_status": row[5],
                    "verification_status": row[6], "source": row[7],
                } for row in rows if row[1])
                if candidates:
                    return candidates[:limit]

            for table, time_column, source_column, verification_column in (
                ("canonical_market_records", "generated_at", "primary_source", "verification_status"),
                ("data_records", "fetched_at", "source_name", "verified"),
            ):
                if not _table_exists(connection, table):
                    continue
                rows = connection.execute(f"""
                    SELECT symbol, payload_json, {source_column}, {verification_column}
                    FROM {table}
                    WHERE UPPER(symbol) LIKE ?
                       OR COALESCE(TRY(json_extract_string(payload_json, '$.name')), '') ILIKE ?
                       OR COALESCE(TRY(json_extract_string(payload_json, '$.short_name')), '') ILIKE ?
                       OR COALESCE(TRY(json_extract_string(payload_json, '$.company_name')), '') ILIKE ?
                       OR COALESCE(TRY(json_extract_string(payload_json, '$.fullname')), '') ILIKE ?
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY {time_column} DESC) = 1
                    LIMIT ?
                """, [code_prefix, contains, contains, contains, contains, limit * 3]).fetchall()
                for symbol, raw_payload, source, verification in rows:
                    payload = _json(raw_payload)
                    if not isinstance(payload, dict):
                        continue
                    name = payload.get("name") or payload.get("short_name") or payload.get("code_name")
                    if not name:
                        continue
                    status = payload.get("listing_status") or payload.get("list_status") or "UNKNOWN"
                    candidates.append({
                        "symbol": symbol, "name": name,
                        "company_name": payload.get("company_name") or payload.get("fullname"),
                        "exchange": symbol.split(".")[-1], "board": payload.get("board"),
                        "listing_status": {"L": "ACTIVE", "D": "DELISTED", "P": "PAUSED"}.get(status, status),
                        "verification_status": str(verification), "source": source,
                    })

        unique: dict[str, dict[str, Any]] = {}
        for item in candidates:
            unique.setdefault(item["symbol"], item)
        def rank(item: dict[str, Any]) -> tuple[int, int, str]:
            symbol = str(item["symbol"]).upper()
            name = str(item.get("name") or "")
            exact = 0 if symbol == upper or symbol.split(".")[0] == upper else 1 if name == keyword else 2 if name.startswith(keyword) else 3
            inactive = 0 if str(item.get("listing_status") or "").upper() in {"ACTIVE", "LISTED", "L"} else 1
            return exact, inactive, symbol
        return sorted(unique.values(), key=rank)[:limit]

    def dragon_tiger_board(self, *, limit: int = 50) -> dict[str, Any]:
        """Read the latest persisted post-close Dragon-Tiger events."""
        from database.db import get_connection

        with get_connection(read_only=True) as connection:
            if not _table_exists(connection, "sentiment_v3_snapshots"):
                raise KeyError("no dragon tiger snapshots")
            trade_date = connection.execute("""
                SELECT MAX(trade_date)
                FROM sentiment_v3_snapshots
                WHERE snapshot_type = 'DRAGON_TIGER'
                  AND record_source = 'REAL'
                  AND TRY_CAST(json_extract(payload_json, '$.has_dragon_tiger') AS BOOLEAN) IS TRUE
            """).fetchone()[0]
            if trade_date is None:
                raise KeyError("no dragon tiger events")
            event_total = int(connection.execute("""
                SELECT COUNT(DISTINCT symbol_scope)
                FROM sentiment_v3_snapshots
                WHERE snapshot_type = 'DRAGON_TIGER' AND record_source = 'REAL'
                  AND trade_date = ?
                  AND TRY_CAST(json_extract(payload_json, '$.has_dragon_tiger') AS BOOLEAN) IS TRUE
            """, [trade_date]).fetchone()[0])
            rows = connection.execute("""
                WITH latest_identity AS (
                    SELECT symbol, COALESCE(short_name, company_name) AS name,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY updated_at DESC) AS rn
                    FROM stock_universe
                )
                SELECT s.symbol_scope, i.name, s.available_time, s.source,
                       s.quality_flags_json, s.payload_json
                FROM sentiment_v3_snapshots s
                LEFT JOIN latest_identity i ON i.symbol = s.symbol_scope AND i.rn = 1
                WHERE s.snapshot_type = 'DRAGON_TIGER'
                  AND s.record_source = 'REAL' AND s.trade_date = ?
                  AND TRY_CAST(json_extract(s.payload_json, '$.has_dragon_tiger') AS BOOLEAN) IS TRUE
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY s.symbol_scope ORDER BY s.revision DESC, s.created_at DESC
                ) = 1
                ORDER BY ABS(COALESCE(TRY_CAST(json_extract(s.payload_json, '$.net_amount') AS DOUBLE), 0)) DESC,
                         s.symbol_scope
                LIMIT ?
            """, [trade_date, limit]).fetchall()
        items = []
        for symbol, name, available_time, source, raw_flags, raw_payload in rows:
            payload = _json(raw_payload) or {}
            items.append({
                "symbol": symbol, "name": name, "reasons": payload.get("reasons") or [],
                "buy_amount": payload.get("buy_amount"), "sell_amount": payload.get("sell_amount"),
                "net_amount": payload.get("net_amount"),
                "institution_buy_amount": payload.get("institution_buy_amount"),
                "institution_sell_amount": payload.get("institution_sell_amount"),
                "institution_net_amount": payload.get("institution_net_amount"),
                "seat_count": payload.get("seat_count"),
                "institution_seat_count": payload.get("institution_seat_count"),
                "top_buyers": payload.get("top_buyers") or [],
                "top_sellers": payload.get("top_sellers") or [],
            })
        return {
            "trade_date": str(trade_date),
            "available_time": None if not rows else rows[0][2],
            "source": None if not rows else rows[0][3],
            "quality_flags": [] if not rows else (_json(rows[0][4]) or []),
            "publication_type": "POST_CLOSE_EOD",
            "is_realtime": False,
            "items": items,
            "total": event_total,
        }

    def stock(self, symbol: str) -> dict[str, Any]:
        from database.db import get_connection

        canonical = normalize_symbol(symbol)
        if canonical is None:
            raise ValueError(f"invalid A-share symbol: {symbol}")
        with get_connection(read_only=True) as connection:
            row = connection.execute("""
                SELECT symbol, short_name, company_name, exchange, market, board,
                       list_date, listing_status, is_st, is_suspended,
                       primary_source, verification_status, updated_at
                FROM stock_universe WHERE symbol = ?
                ORDER BY updated_at DESC LIMIT 1
            """, [canonical]).fetchone()
            if row is not None:
                identity = {
                    "symbol": row[0], "name": row[1] or row[2], "company_name": row[2],
                    "exchange": row[3], "market": row[4], "board": row[5],
                    "list_date": row[6], "listing_status": row[7], "is_st": bool(row[8]),
                    "is_suspended": bool(row[9]), "source": row[10],
                    "verification_status": row[11], "updated_at": row[12],
                }
            else:
                identity = {"symbol": canonical, "name": None, "company_name": None,
                    "exchange": canonical.split(".")[1], "market": "A_SHARE", "board": None,
                    "list_date": None, "listing_status": "UNKNOWN", "is_st": False,
                    "is_suspended": False, "source": None,
                    "verification_status": "UNAVAILABLE", "updated_at": None}
            if not identity["name"]:
                for table, time_column in (("canonical_market_records", "generated_at"), ("data_records", "fetched_at")):
                    if not _table_exists(connection, table):
                        continue
                    source_column = "primary_source" if table == "canonical_market_records" else "source_name"
                    verification_column = "verification_status" if table == "canonical_market_records" else "verified"
                    nr = connection.execute(f"""
                        SELECT payload_json, {source_column}, {verification_column}, {time_column}
                        FROM {table} WHERE symbol = ?
                        ORDER BY {time_column} DESC LIMIT 1
                    """, [canonical]).fetchone()
                    payload = _json(nr[0]) if nr else None
                    if isinstance(payload, dict) and (payload.get("name") or payload.get("short_name") or payload.get("code_name")):
                        identity["name"] = payload.get("name") or payload.get("short_name") or payload.get("code_name")
                        identity["company_name"] = payload.get("company_name") or payload.get("fullname")
                        identity["list_date"] = payload.get("list_date") or payload.get("list_date_raw")
                        raw_status = payload.get("listing_status") or payload.get("list_status")
                        identity["listing_status"] = {"L": "ACTIVE", "D": "DELISTED", "P": "PAUSED"}.get(raw_status, raw_status or "UNKNOWN")
                        identity["delist_date"] = payload.get("delist_date")
                        identity["source"] = nr[1] or table
                        identity["verification_status"] = (
                            str(nr[2]) if table == "canonical_market_records"
                            else ("VERIFIED" if bool(nr[2]) else "UNVERIFIED")
                        )
                        identity["updated_at"] = nr[3]
                        break
            industry = None
            if _table_exists(connection, "stock_industry_memberships"):
                ir = connection.execute("""
                    SELECT industry_code, industry_name, industry_level,
                           classification_system, source, verification_status, generated_at
                    FROM stock_industry_memberships WHERE symbol = ?
                    ORDER BY CASE WHEN valid_to IS NULL THEN 0 ELSE 1 END,
                             generated_at DESC LIMIT 1
                """, [canonical]).fetchone()
                if ir:
                    industry = {"code": ir[0], "name": ir[1], "level": ir[2],
                        "classification_system": ir[3], "source": ir[4],
                        "verification_status": ir[5], "generated_at": ir[6]}
            qr = connection.execute("""
                SELECT price, previous_close, open, high, low, volume,
                       amount * CASE
                           WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'CNY' THEN 1
                           WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'THOUSAND_CNY' THEN 1000
                           WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'TEN_THOUSAND_CNY' THEN 10000
                           WHEN source = 'Tushare' THEN 1000
                           WHEN source = 'Tencent' THEN 10000 ELSE 1 END AS amount_cny,
                       change_pct, turnover_rate, snapshot_time, source,
                       is_suspended, is_abnormal
                FROM market_snapshot_items WHERE symbol = ?
                ORDER BY snapshot_time DESC LIMIT 1
            """, [canonical]).fetchone()
            quote = None if qr is None else {
                "price": qr[0], "previous_close": qr[1], "open": qr[2], "high": qr[3],
                "low": qr[4], "volume": qr[5], "amount": qr[6], "change_pct": qr[7],
                "turnover_rate": qr[8], "snapshot_time": qr[9], "source": qr[10],
                "is_suspended": bool(qr[11]), "is_abnormal": bool(qr[12])}
            factors: list[dict[str, Any]] = []
            if _table_exists(connection, "factor_outputs"):
                rows = connection.execute("""
                    SELECT factor_type, score, confidence, data_cutoff, generated_at,
                           algorithm_version, shadow_mode, risk_flags_json
                    FROM factor_outputs WHERE symbol = ?
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY factor_type
                        ORDER BY data_cutoff DESC, generated_at DESC) = 1
                    ORDER BY factor_type
                """, [canonical]).fetchall()
                factors = [{"type": r[0], "score": float(r[1]), "confidence": float(r[2]),
                    "data_cutoff": r[3], "generated_at": r[4], "algorithm_version": r[5],
                    "formal_status": "shadow" if r[6] else "formal",
                    "risk_flags": _json(r[7]) or []} for r in rows]
            present = {item["type"] for item in factors}
            snapshot_sources = (
                ("SENTIMENT", "sentiment_symbol_snapshots", "score"),
                ("POLICY_NEWS", "policy_news_symbol_snapshots", "weighted_policy_score"),
                ("CAPITAL_FLOW", "capital_flow_symbol_snapshots", "score"),
            )
            for factor_type, table, score_column in snapshot_sources:
                if factor_type in present or not _table_exists(connection, table):
                    continue
                sr = connection.execute(f"""
                    SELECT {score_column}, confidence, data_cutoff, generated_at,
                           algorithm_version, shadow_mode, risk_flags_json
                    FROM {table} WHERE symbol = ?
                    ORDER BY data_cutoff DESC, generated_at DESC LIMIT 1
                """, [canonical]).fetchone()
                if sr:
                    factors.append({"type": factor_type, "score": float(sr[0]),
                        "confidence": float(sr[1]), "data_cutoff": sr[2],
                        "generated_at": sr[3], "algorithm_version": sr[4],
                        "formal_status": "shadow" if sr[5] else "formal",
                        "risk_flags": _json(sr[6]) or []})
            financials: list[dict[str, Any]] = []
            if _table_exists(connection, "canonical_financial_records"):
                rows = connection.execute("""
                    SELECT data_type, event_time, data_cutoff, primary_source,
                           verification_status, confidence, payload_json, algorithm_version
                    FROM canonical_financial_records WHERE symbol = ?
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY data_type
                        ORDER BY data_cutoff DESC, generated_at DESC) = 1
                    ORDER BY data_type LIMIT 8
                """, [canonical]).fetchall()
                financials = [{"data_type": r[0], "event_time": r[1], "data_cutoff": r[2],
                    "source": r[3], "verification_status": r[4], "confidence": float(r[5]),
                    "metrics": _json(r[6]), "algorithm_version": r[7]} for r in rows]
        if not identity["name"]:
            raise KeyError(f"stock name not found in database: {canonical}")
        return {"symbol": canonical, "identity": identity, "industry": industry,
            "quote": quote, "factors": factors, "financials": financials,
            "source": "router_main_database", "calculation": "persisted_strategy_outputs"}


__all__ = ["EquityContextReader"]
