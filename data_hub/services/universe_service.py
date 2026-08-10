from __future__ import annotations

from datetime import datetime, time, timedelta
from time import perf_counter
from typing import Any

import pandas as pd

from data_hub.providers.full_market import (
    AKShareBatchProvider,
    BaoStockBatchProvider,
)
from data_hub.repositories.full_market import FullMarketRepository
from data_hub.schemas.full_market import (
    AliasType,
    IndustryMembership,
    ListingStatus,
    StockAlias,
    StockUniverseRecord,
    UniverseSyncRequest,
    UniverseSyncResponse,
    VerificationStatus,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.full_market_common import (
    SHANGHAI_TZ,
    board_for,
    clean_text,
    is_a_share_symbol,
    normalize_alias,
    normalize_symbol,
    parse_date,
    price_limit_type,
    stable_hash,
)
from data_hub.services.provider_capability_registry import (
    ProviderCapabilityRegistry,
)


UNIVERSE_ALGORITHM_VERSION = "stock-universe-v1"
INDUSTRY_MAPPING_VERSION = "baostock-industry-v1"


def _is_st(name: str | None) -> bool:
    normalized = normalize_alias(name or "").upper()
    return normalized.startswith(("ST", "*ST", "SST", "S*ST"))


def _record_id(source: str, symbol: str, payload: dict[str, Any]) -> str:
    return f"raw_{stable_hash({'source': source, 'symbol': symbol, 'payload': payload})[:32]}"


def _raw_record(
    *,
    source: str,
    source_level: SourceLevel,
    symbol: str,
    payload: dict[str, Any],
    fetched_at: datetime,
    verified: bool,
) -> MarketRecord:
    digest = stable_hash(
        {
            "source": source,
            "symbol": symbol,
            "data_type": DataType.STOCK_BASIC.value,
            "payload": payload,
        }
    )
    list_date = parse_date(payload.get("ipoDate") or payload.get("list_date"))
    event_time = (
        datetime.combine(list_date, time.min, tzinfo=SHANGHAI_TZ)
        if list_date is not None
        else fetched_at
    )
    return MarketRecord(
        record_id=_record_id(source, symbol, payload),
        symbol=symbol,
        data_type=DataType.STOCK_BASIC,
        event_time=event_time,
        fetched_at=fetched_at,
        source_name=source,
        source_level=source_level,
        verified=verified,
        content_hash=digest,
        data=payload,
    )


class StockUniverseService:
    def __init__(
        self,
        *,
        repository: FullMarketRepository | None = None,
        akshare_provider: AKShareBatchProvider | None = None,
        baostock_provider: BaoStockBatchProvider | None = None,
        capability_registry: ProviderCapabilityRegistry | None = None,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or FullMarketRepository()
        self.akshare = akshare_provider or AKShareBatchProvider()
        self.baostock = baostock_provider or BaoStockBatchProvider()
        self.capabilities = capability_registry or ProviderCapabilityRegistry(
            self.repository
        )
        self.clock = clock or (lambda: datetime.now().astimezone())

    @staticmethod
    def _frame_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
        return [
            {str(key): value for key, value in row.items()}
            for row in frame.to_dict(orient="records")
        ]

    def sync(self, request: UniverseSyncRequest) -> UniverseSyncResponse:
        started_at = self.clock()
        started = perf_counter()
        if request.data_cutoff < started_at - timedelta(minutes=5):
            raise ValueError(
                "current provider sync cannot reconstruct a historical universe; "
                "use a persisted universe version for point-in-time analysis"
            )
        if request.request_budget < 1:
            raise ValueError("stock-universe sync requires at least one batch request")

        request_count = 0
        warnings: list[str] = []
        ak_rows: list[dict[str, Any]] = []
        bao_rows: list[dict[str, Any]] = []
        industry_rows: list[dict[str, Any]] = []

        try:
            result = self.akshare.fetch_stock_list()
            request_count += result.request_count
            ak_rows = self._frame_rows(result.frame)
            self.capabilities.record(
                provider=result.provider,
                capability=result.capability,
                available=True,
                verified_at=started_at,
                batch_supported=True,
                maximum_batch_size=len(result.frame),
                fallback_provider="BaoStock",
                metadata=result.metadata,
            )
        except Exception as exc:
            self.capabilities.record(
                provider="AKShare",
                capability="FULL_STOCK_LIST",
                available=False,
                verified_at=started_at,
                batch_supported=True,
                failure_reason=f"{type(exc).__name__}: {exc}",
                fallback_provider="BaoStock",
            )
            warnings.append(f"AKShare stock list unavailable: {type(exc).__name__}")

        if request_count < request.request_budget:
            try:
                result = self.baostock.fetch_stock_basic()
                request_count += result.request_count
                bao_rows = self._frame_rows(result.frame)
                self.capabilities.record(
                    provider=result.provider,
                    capability=result.capability,
                    available=True,
                    verified_at=started_at,
                    batch_supported=True,
                    maximum_batch_size=len(result.frame),
                    fallback_provider="AKShare",
                    metadata=result.metadata,
                )
            except Exception as exc:
                self.capabilities.record(
                    provider="BaoStock",
                    capability="FULL_STOCK_BASIC",
                    available=False,
                    verified_at=started_at,
                    batch_supported=True,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                    fallback_provider="AKShare",
                )
                warnings.append(f"BaoStock stock basic unavailable: {type(exc).__name__}")

        if request_count < request.request_budget:
            try:
                result = self.baostock.fetch_industry_memberships()
                request_count += result.request_count
                industry_rows = self._frame_rows(result.frame)
                self.capabilities.record(
                    provider=result.provider,
                    capability=result.capability,
                    available=True,
                    verified_at=started_at,
                    batch_supported=True,
                    maximum_batch_size=len(result.frame),
                    metadata=result.metadata,
                )
            except Exception as exc:
                self.capabilities.record(
                    provider="BaoStock",
                    capability="FULL_INDUSTRY_MEMBERSHIPS",
                    available=False,
                    verified_at=started_at,
                    batch_supported=True,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
                warnings.append(f"BaoStock industry unavailable: {type(exc).__name__}")

        if not ak_rows and not bao_rows:
            raise RuntimeError("no verified provider returned a stock universe")

        current_names: dict[str, str] = {}
        for row in ak_rows:
            symbol = normalize_symbol(row.get("code"))
            name = clean_text(row.get("name"))
            if symbol and is_a_share_symbol(symbol) and name:
                current_names[symbol] = name

        bao_by_symbol: dict[str, dict[str, Any]] = {}
        for row in bao_rows:
            if clean_text(row.get("type")) != "1":
                continue
            symbol = normalize_symbol(row.get("code"))
            if symbol is not None and is_a_share_symbol(symbol):
                bao_by_symbol[symbol] = row

        symbols = sorted(set(current_names) | set(bao_by_symbol))
        raw_records: list[MarketRecord] = []
        raw_ids: dict[str, list[str]] = {}
        source_payloads: dict[str, dict[str, dict[str, Any]]] = {}
        for symbol in symbols:
            source_payloads[symbol] = {}
            ak_name = current_names.get(symbol)
            bao = bao_by_symbol.get(symbol)
            bao_name = clean_text(bao.get("code_name")) if bao else ""
            names_match = bool(
                ak_name
                and bao_name
                and normalize_alias(ak_name) == normalize_alias(bao_name)
            )
            if ak_name:
                payload = {
                    "symbol": symbol,
                    "code": symbol.split(".", 1)[0],
                    "name": ak_name,
                    "listing_status": "ACTIVE",
                }
                raw = _raw_record(
                    source="AKShare / stock_info_a_code_name",
                    source_level=SourceLevel.PUBLIC_WEB,
                    symbol=symbol,
                    payload=payload,
                    fetched_at=started_at,
                    verified=names_match,
                )
                raw_records.append(raw)
                raw_ids.setdefault(symbol, []).append(raw.record_id)
                source_payloads[symbol]["AKShare"] = payload
            if bao:
                payload = {
                    "symbol": symbol,
                    "raw_code": clean_text(bao.get("code")),
                    "name": bao_name,
                    "list_date": clean_text(bao.get("ipoDate")),
                    "out_date": clean_text(bao.get("outDate")),
                    "security_type": clean_text(bao.get("type")),
                    "status": clean_text(bao.get("status")),
                }
                raw = _raw_record(
                    source="BaoStock / query_stock_basic",
                    source_level=SourceLevel.STRUCTURED,
                    symbol=symbol,
                    payload=payload,
                    fetched_at=started_at,
                    verified=names_match,
                )
                raw_records.append(raw)
                raw_ids.setdefault(symbol, []).append(raw.record_id)
                source_payloads[symbol]["BaoStock"] = payload

        stable_records: list[dict[str, Any]] = []
        for symbol in symbols:
            bao = bao_by_symbol.get(symbol, {})
            short_name = current_names.get(symbol) or clean_text(bao.get("code_name")) or None
            list_date = parse_date(bao.get("ipoDate"))
            delist_date = parse_date(bao.get("outDate"))
            if symbol in current_names:
                status = ListingStatus.ACTIVE
            elif delist_date is not None:
                status = ListingStatus.DELISTED
            else:
                status = ListingStatus.INACTIVE
            ak_name = current_names.get(symbol)
            bao_name = clean_text(bao.get("code_name"))
            differences: dict[str, Any] = {}
            if (
                ak_name
                and bao_name
                and normalize_alias(ak_name) != normalize_alias(bao_name)
            ):
                differences["short_name"] = {
                    "AKShare": ak_name,
                    "BaoStock": bao_name,
                }
            verification = (
                VerificationStatus.VERIFIED
                if ak_name and bao_name and not differences
                else (
                    VerificationStatus.CONFLICT
                    if differences
                    else VerificationStatus.SINGLE_SOURCE
                )
            )
            stable_records.append(
                {
                    "symbol": symbol,
                    "exchange": symbol.split(".", 1)[1],
                    "market": "A_SHARE",
                    "board": board_for(symbol),
                    "security_type": "STOCK",
                    "company_name": None,
                    "short_name": short_name,
                    "list_date": list_date,
                    "delist_date": delist_date,
                    "listing_status": status.value,
                    "is_st": _is_st(short_name),
                    "is_suspended": False,
                    "currency": "CNY",
                    "price_limit_type": price_limit_type(symbol, _is_st(short_name)),
                    "primary_source": (
                        "BaoStock" if symbol in bao_by_symbol else "AKShare"
                    ),
                    "source_record_ids": raw_ids.get(symbol, []),
                    "verification_status": verification.value,
                    "source_differences": differences,
                }
            )

        content_hash = stable_hash(
            {
                "algorithm": UNIVERSE_ALGORITHM_VERSION,
                "records": stable_records,
            }
        )
        version = f"universe_{content_hash[:16]}"
        records = [
            StockUniverseRecord(
                **item,
                data_available_time=started_at,
                updated_at=started_at,
                universe_version=version,
            )
            for item in stable_records
        ]

        aliases: list[StockAlias] = []
        for item in records:
            if not item.short_name:
                continue
            alias_type = (
                AliasType.HISTORICAL_SHORT_NAME
                if item.listing_status == ListingStatus.DELISTED
                else AliasType.CURRENT_SHORT_NAME
            )
            valid_from = (
                datetime.combine(item.list_date, time.min, tzinfo=SHANGHAI_TZ)
                if item.list_date
                else None
            )
            valid_to = (
                datetime.combine(item.delist_date, time.max, tzinfo=SHANGHAI_TZ)
                if item.delist_date
                else None
            )
            alias_identity = {
                "symbol": item.symbol,
                "alias": normalize_alias(item.short_name),
                "type": alias_type.value,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "version": version,
            }
            aliases.append(
                StockAlias(
                    alias_id=f"alias_{stable_hash(alias_identity)[:32]}",
                    symbol=item.symbol,
                    alias_name=item.short_name,
                    alias_type=alias_type,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    source=item.primary_source,
                    verification_status=item.verification_status,
                    normalized_alias=normalize_alias(item.short_name),
                    generated_at=started_at,
                    universe_version=version,
                )
            )

        valid_symbols = set(symbols)
        industries: list[IndustryMembership] = []
        for row in industry_rows:
            symbol = normalize_symbol(row.get("code"))
            industry = clean_text(row.get("industry"))
            classification = clean_text(row.get("industryClassification"))
            if symbol not in valid_symbols or not industry or not classification:
                continue
            updated = parse_date(row.get("updateDate"))
            valid_from = (
                datetime.combine(updated, time.min, tzinfo=SHANGHAI_TZ)
                if updated
                else None
            )
            identity = {
                "symbol": symbol,
                "industry": industry,
                "classification": classification,
                "valid_from": valid_from,
                "version": INDUSTRY_MAPPING_VERSION,
            }
            industries.append(
                IndustryMembership(
                    membership_id=f"industry_{stable_hash(identity)[:32]}",
                    symbol=symbol,
                    industry_code=None,
                    industry_name=industry,
                    industry_level="PROVIDER_REPORTED",
                    classification_system=classification,
                    valid_from=valid_from,
                    valid_to=None,
                    source="BaoStock / query_stock_industry",
                    verification_status=VerificationStatus.SINGLE_SOURCE,
                    mapping_version=INDUSTRY_MAPPING_VERSION,
                    generated_at=started_at,
                )
            )

        completed_at = self.clock()
        conflict_count = sum(
            item.verification_status == VerificationStatus.CONFLICT
            for item in records
        )
        run_id = f"usr_{stable_hash({'version': version, 'started': started_at})[:24]}"
        persisted_count = 0
        if not request.dry_run:
            persisted_count = self.repository.save_universe(
                run_id=run_id,
                provider=request.provider,
                request_budget=request.request_budget,
                request_count=request_count,
                data_cutoff=request.data_cutoff,
                started_at=started_at,
                completed_at=completed_at,
                universe_version=version,
                content_hash=content_hash,
                records=records,
                aliases=aliases,
                industries=industries,
                raw_records=raw_records,
                conflict_count=conflict_count,
                failed_count=0,
                warnings=warnings,
                report_path=request.report_path,
            )
            self.capabilities.persist()
        board_counts: dict[str, int] = {}
        for item in records:
            board_counts[item.board] = board_counts.get(item.board, 0) + 1
        return UniverseSyncResponse(
            run_id=run_id,
            mode="DRY_RUN" if request.dry_run else "APPLY",
            provider=request.provider,
            request_count=request_count,
            universe_version=version,
            received_count=len(records),
            persisted_count=persisted_count,
            active_count=sum(
                item.listing_status == ListingStatus.ACTIVE for item in records
            ),
            conflict_count=conflict_count,
            failed_count=0,
            board_counts=board_counts,
            alias_count=len(aliases),
            industry_count=len(industries),
            status="SUCCESS" if records else "FAILED",
            warnings=warnings,
            elapsed_seconds=max(0.0, perf_counter() - started),
        )


__all__ = [
    "INDUSTRY_MAPPING_VERSION",
    "StockUniverseService",
    "UNIVERSE_ALGORITHM_VERSION",
]
