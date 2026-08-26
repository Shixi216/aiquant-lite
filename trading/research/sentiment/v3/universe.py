from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.schemas import (
    MarketUniverse,
    SectorMembership,
    UNIVERSE_RULES_VERSION,
    UniverseScope,
    UniverseSecurity,
)


BASE_UNIVERSE_VERSION = "A_SHARE_BASE_V1"


def _digest(value: object) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_market_universe(
    *,
    trade_date: date,
    as_of: datetime,
    securities: Iterable[UniverseSecurity],
    observed_symbols: Iterable[str],
    source: str,
    source_universe_version: str | None,
    scope: UniverseScope = UniverseScope.BREADTH_ELIGIBLE,
    memberships: Iterable[SectorMembership] = (),
    created_at: datetime | None = None,
) -> MarketUniverse:
    """Build a small, explicit A-share universe family.

    BASE includes ordinary SH/SZ/BJ stocks, including ST, new listings and
    suspended names. BREADTH excludes only confirmed suspended names.
    SECTOR additionally requires an available sector membership.
    """

    records: dict[str, UniverseSecurity] = {}
    ignored_non_stock = 0
    ignored_not_listed = 0
    for item in securities:
        if item.security_type != "STOCK" or item.exchange not in {"SH", "SZ", "BJ"}:
            ignored_non_stock += 1
            continue
        if item.list_date is not None and item.list_date > trade_date:
            ignored_not_listed += 1
            continue
        if item.delist_date is not None and item.delist_date < trade_date:
            ignored_not_listed += 1
            continue
        if item.listing_status not in {"ACTIVE", "L", "LISTED"}:
            ignored_not_listed += 1
            continue
        records[item.symbol] = item

    base = set(records)
    eligible = set(base)
    exclusions: dict[str, int] = {
        "NON_A_SHARE_OR_NON_STOCK_INPUT": ignored_non_stock,
        "NOT_LISTED_ON_TRADE_DATE": ignored_not_listed,
    }
    if scope in {UniverseScope.BREADTH_ELIGIBLE, UniverseScope.SECTOR_ELIGIBLE}:
        suspended = {symbol for symbol in eligible if records[symbol].is_suspended}
        eligible -= suspended
        exclusions["CONFIRMED_SUSPENDED"] = len(suspended)
    if scope == UniverseScope.SECTOR_ELIGIBLE:
        membership_symbols = {
            item.symbol
            for item in memberships
            if item.available_time <= as_of
            and (item.effective_from is None or item.effective_from <= as_of)
            and (item.effective_to is None or item.effective_to >= as_of)
        }
        missing = eligible - membership_symbols
        eligible -= missing
        exclusions["MISSING_SECTOR_MEMBERSHIP"] = len(missing)

    symbols = sorted(eligible)
    observed = set(observed_symbols) & eligible
    symbol_hash = _digest(symbols)
    identity = {
        "base_version": BASE_UNIVERSE_VERSION,
        "rules_version": UNIVERSE_RULES_VERSION,
        "scope": scope,
        "trade_date": trade_date,
        "source_universe_version": source_universe_version,
        "symbols": symbols,
    }
    identity_hash = _digest(identity)
    eligible_count = len(symbols)
    return MarketUniverse(
        universe_id=f"sv3_universe_{identity_hash[:32]}",
        universe_version=f"{BASE_UNIVERSE_VERSION}_{identity_hash[:16]}",
        universe_scope=scope,
        trade_date=trade_date,
        as_of=as_of,
        expected_symbol_count=len(base),
        eligible_symbol_count=eligible_count,
        observed_symbol_count=len(observed),
        excluded_symbol_count=len(base) - eligible_count,
        coverage_ratio=(len(observed) / eligible_count if eligible_count else 0.0),
        source=source,
        rules_version=UNIVERSE_RULES_VERSION,
        exclusion_summary=dict(sorted(exclusions.items())),
        symbols=symbols,
        symbol_hash=symbol_hash,
        source_universe_version=source_universe_version,
        created_at=created_at or as_of,
    )


__all__ = ["BASE_UNIVERSE_VERSION", "build_market_universe"]
