"""Read-only reader over the formal four-dimension cross-section snapshot.

The main table ``cross_section_factor_snapshot`` only ever holds the latest
section (DELETE + rewrite by ``compute_cross_section_snapshot.py``), so the
"latest" endpoints read it directly.
"""
from __future__ import annotations

import duckdb

from data_hub.services.full_market_common import normalize_symbol
from trading.decision_support.formal_score_resolver import (
    CROSS_SECTION_SOURCE,
    FormalScoreResolver,
)
from trading.decision_support.schemas_cross_section import (
    CrossSectionItem,
    CrossSectionLatestResponse,
    CrossSectionSymbolResponse,
    CrossSectionThresholds,
)

_SORT_COLUMNS = {
    "composite": "composite_score",
    "tech": "tech_rank",
    "fund": "fund_rank",
    "sent": "sent_rank",
    "margin": "margin_rank",
}

_SELECT_SQL = """
    SELECT snapshot_id, symbol, data_cutoff, tech_rank, fund_rank,
           sent_rank, margin_rank, composite_score, generated_at
    FROM cross_section_factor_snapshot
    WHERE composite_score IS NOT NULL {extra_clause}
    ORDER BY {sort_column} DESC, symbol ASC
    NULLS LAST
    LIMIT ? OFFSET ?
"""


class CrossSectionReader:
    """Read the formal cross-section snapshot (never computes scores)."""

    def __init__(self, resolver: FormalScoreResolver | None = None) -> None:
        self._resolver = resolver or FormalScoreResolver()

    def latest(
        self,
        *,
        limit: int,
        offset: int,
        min_composite: float | None,
        sort: str,
    ) -> CrossSectionLatestResponse:
        from database.db import get_connection

        sort_column = _SORT_COLUMNS[sort]
        extra_clause = ""
        parameters: list = []
        if min_composite is not None:
            extra_clause = "AND composite_score >= ?"
            parameters.append(min_composite)
        with get_connection(read_only=True) as connection:
            try:
                rows = connection.execute(
                    _SELECT_SQL.format(
                        extra_clause=extra_clause, sort_column=sort_column
                    ),
                    [*parameters, limit, offset],
                ).fetchall()
                total = connection.execute(
                    "SELECT COUNT(*) FROM cross_section_factor_snapshot "
                    "WHERE composite_score IS NOT NULL " + extra_clause,
                    parameters,
                ).fetchone()[0]
                thresholds = self._latest_thresholds(connection)
                meta = connection.execute(
                    """
                    SELECT MAX(data_cutoff), MAX(generated_at)
                    FROM cross_section_factor_snapshot
                    """
                ).fetchone()
            except duckdb.CatalogException as exc:
                raise KeyError("cross_section_factor_snapshot is empty") from exc
        if int(total) == 0:
            raise KeyError("cross_section_factor_snapshot is empty")
        identities = self._identities(connection=None, symbols=[row[1] for row in rows])
        items = [
            CrossSectionItem(
                symbol=row[1],
                name=identities.get(row[1], {}).get("name"),
                industry=identities.get(row[1], {}).get("industry"),
                tech_rank=row[3],
                fund_rank=row[4],
                sent_rank=row[5],
                margin_rank=row[6],
                composite_score=float(row[7]),
            )
            for row in rows
        ]
        return CrossSectionLatestResponse(
            data_cutoff=meta[0],
            generated_at=meta[1],
            total=int(total),
            offset=offset,
            limit=limit,
            sort=sort,
            min_composite=min_composite,
            thresholds=thresholds,
            items=items,
        )

    @staticmethod
    def _identities(connection, symbols: list[str]) -> dict[str, dict[str, str | None]]:
        if not symbols:
            return {}
        from database.db import get_connection

        owns_connection = connection is None
        context = get_connection(read_only=True) if owns_connection else None
        conn = context.__enter__() if context is not None else connection
        try:
            has_universe = bool(conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='stock_universe'"
            ).fetchone()[0])
            if not has_universe:
                return {}
            placeholders = ",".join("?" for _ in symbols)
            name_rows = conn.execute(f"""
                SELECT symbol, COALESCE(NULLIF(TRIM(short_name), ''), company_name)
                FROM stock_universe WHERE symbol IN ({placeholders})
                QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY updated_at DESC) = 1
            """, symbols).fetchall()
            result = {row[0]: {"name": row[1], "industry": None} for row in name_rows}
            unresolved = [symbol for symbol in symbols if not result.get(symbol, {}).get("name")]
            for table, time_column in (("canonical_market_records", "generated_at"), ("data_records", "fetched_at")):
                if not unresolved:
                    break
                exists = bool(conn.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]
                ).fetchone()[0])
                if not exists:
                    continue
                marks = ",".join("?" for _ in unresolved)
                rows = conn.execute(f"""
                    SELECT symbol, COALESCE(
                        TRY(json_extract_string(payload_json, '$.name')),
                        TRY(json_extract_string(payload_json, '$.short_name')),
                        TRY(json_extract_string(payload_json, '$.code_name')))
                    FROM {table} WHERE symbol IN ({marks})
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY {time_column} DESC) = 1
                """, unresolved).fetchall()
                for stock_symbol, name in rows:
                    if name:
                        result.setdefault(stock_symbol, {"name": None, "industry": None})["name"] = name
                unresolved = [symbol for symbol in unresolved if not result.get(symbol, {}).get("name")]
            has_industry = bool(conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='stock_industry_memberships'"
            ).fetchone()[0])
            if has_industry:
                industry_rows = conn.execute(f"""
                    SELECT symbol, industry_name FROM stock_industry_memberships
                    WHERE symbol IN ({placeholders})
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY
                        CASE WHEN valid_to IS NULL THEN 0 ELSE 1 END, generated_at DESC) = 1
                """, symbols).fetchall()
                for stock_symbol, industry in industry_rows:
                    result.setdefault(stock_symbol, {"name": None, "industry": None})["industry"] = industry
            return result
        finally:
            if context is not None:
                context.__exit__(None, None, None)

    @staticmethod
    def _latest_thresholds(
        connection,
    ) -> CrossSectionThresholds | None:
        try:
            row = connection.execute(
                """
                SELECT data_cutoff, p50, p75, p90, p95,
                       strong_buy_q, buy_q, n_total, generated_at
                FROM cross_section_thresholds
                ORDER BY generated_at DESC
                LIMIT 1
                """
            ).fetchone()
        except duckdb.CatalogException:
            return None
        if row is None:
            return None
        return CrossSectionThresholds(
            data_cutoff=row[0],
            p50=float(row[1]),
            p75=float(row[2]),
            p90=float(row[3]),
            p95=float(row[4]),
            strong_buy_q=float(row[5]),
            buy_q=float(row[6]),
            n_total=int(row[7]),
            generated_at=row[8],
        )

    def for_symbol(self, symbol: str) -> CrossSectionSymbolResponse:
        canonical = normalize_symbol(symbol)
        if canonical is None:
            raise ValueError(f"invalid A-share symbol: {symbol}")
        result = self._resolver.read_cross_section(canonical, with_detail=True)
        if result is None:
            raise KeyError(f"no cross-section score for {symbol}")
        score, detail = result
        detail = detail or {}
        identity = self._identities(connection=None, symbols=[canonical]).get(canonical, {})
        return CrossSectionSymbolResponse(
            symbol=canonical,
            name=identity.get("name"),
            industry=identity.get("industry"),
            score_source=CROSS_SECTION_SOURCE,
            composite_score=score,
            tech_rank=detail.get("tech_rank"),
            fund_rank=detail.get("fund_rank"),
            sent_rank=detail.get("sent_rank"),
            margin_rank=detail.get("margin_rank"),
        )


__all__ = ["CrossSectionReader"]
