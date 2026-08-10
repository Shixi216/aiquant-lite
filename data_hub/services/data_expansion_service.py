from __future__ import annotations

from datetime import date, datetime, time, timedelta
from time import perf_counter
from typing import Any

import pandas as pd

from data_hub.providers import AKShareProvider, BaoStockProvider
from data_hub.providers.full_market import AKShareBatchProvider
from data_hub.repositories.full_market import FullMarketRepository
from data_hub.schemas.full_market import (
    DataExpansionRequest,
    DataExpansionResponse,
    ExpansionType,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.canonicalization_service import CanonicalizationService
from data_hub.services.event_cluster_service import EventClusterService
from data_hub.services.full_market_common import (
    SHANGHAI_TZ,
    clean_text,
    normalize_symbol,
    stable_hash,
)
from data_hub.services.provider_capability_registry import (
    ProviderCapabilityRegistry,
)


def _event_time(value: Any, *, end_of_day: bool = False) -> datetime | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    result = pd.Timestamp(parsed).to_pydatetime()
    if result.tzinfo is None:
        result = result.replace(tzinfo=SHANGHAI_TZ)
    else:
        result = result.astimezone(SHANGHAI_TZ)
    if end_of_day and result.time() == time.min:
        result = result.replace(hour=23, minute=59, second=59)
    return result


def _raw_id(prefix: str, identity: dict[str, Any]) -> tuple[str, str]:
    digest = stable_hash(identity)
    return f"{prefix}_{digest[:32]}", digest


class DataExpansionService:
    """Budgeted expansion; per-symbol access is restricted to RESEARCH."""

    def __init__(
        self,
        *,
        repository: FullMarketRepository | None = None,
        akshare_daily: AKShareProvider | None = None,
        baostock_daily: BaoStockProvider | None = None,
        batch_provider: AKShareBatchProvider | None = None,
        capability_registry: ProviderCapabilityRegistry | None = None,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or FullMarketRepository()
        self.akshare_daily = akshare_daily or AKShareProvider()
        self.baostock_daily = baostock_daily or BaoStockProvider()
        self.batch_provider = batch_provider or AKShareBatchProvider()
        self.capabilities = capability_registry or ProviderCapabilityRegistry(
            self.repository
        )
        self.clock = clock or (lambda: datetime.now().astimezone())

    def _daily(
        self,
        request: DataExpansionRequest,
        *,
        run_id: str,
    ) -> tuple[list[dict[str, Any]], int, list[str]]:
        start = request.start_date or request.trade_date
        end = request.end_date or request.trade_date
        if start is None or end is None:
            raise ValueError("daily expansion requires a trade date or date range")
        if end > request.data_cutoff.astimezone(SHANGHAI_TZ).date():
            raise ValueError("daily expansion end_date exceeds data_cutoff")
        if not request.symbols:
            raise ValueError(
                "per-symbol daily provider requires an explicit bounded symbol list"
            )
        items: list[dict[str, Any]] = []
        request_count = 0
        warnings: list[str] = []
        for symbol in request.symbols:
            if request_count >= request.request_budget:
                items.append(
                    {
                        "item_key": symbol,
                        "symbol": symbol,
                        "data_type": DataType.DAILY_BAR.value,
                        "status": "SKIPPED_REQUEST_BUDGET",
                        "processed_at": self.clock(),
                    }
                )
                continue
            try:
                if request.provider.upper() == "BAOSTOCK":
                    records = self.baostock_daily.get_daily_bars(
                        symbol,
                        start.strftime("%Y%m%d"),
                        end.strftime("%Y%m%d"),
                    )
                    provider = "BaoStock"
                else:
                    records = self.akshare_daily.get_daily_bars(
                        symbol,
                        start.strftime("%Y%m%d"),
                        end.strftime("%Y%m%d"),
                    )
                    provider = "AKShare"
                request_count += 1
                canonical_ids: list[str] = []
                if not request.dry_run:
                    self.repository.save_raw_records(records)
                    service = CanonicalizationService()
                    for record in records:
                        canonical = service.canonicalize_market([record])
                        canonical_ids.append(canonical.canonical_record_id)
                items.append(
                    {
                        "item_key": symbol,
                        "symbol": symbol,
                        "data_type": DataType.DAILY_BAR.value,
                        "status": "SUCCESS" if records else "SKIPPED_NO_DATA",
                        "source_record_ids": [item.record_id for item in records],
                        "canonical_record_ids": canonical_ids,
                        "request_count": 1,
                        "processed_at": self.clock(),
                    }
                )
                self.capabilities.record(
                    provider=provider,
                    capability="PER_SYMBOL_DAILY",
                    available=True,
                    verified_at=self.clock(),
                    batch_supported=False,
                    maximum_batch_size=1,
                    rate_limit="bounded by request_budget",
                    fallback_provider=(
                        "BaoStock" if provider == "AKShare" else "AKShare"
                    ),
                )
            except Exception as exc:
                request_count += 1
                items.append(
                    {
                        "item_key": symbol,
                        "symbol": symbol,
                        "data_type": DataType.DAILY_BAR.value,
                        "status": "FAILED",
                        "request_count": 1,
                        "processed_at": self.clock(),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],
                    }
                )
        if len(request.symbols) > request.request_budget:
            warnings.append("some symbols were skipped by request_budget")
        return items, request_count, warnings

    def _announcement_records(
        self,
        frame: pd.DataFrame,
        *,
        fetched_at: datetime,
        data_cutoff: datetime,
        limit: int,
    ) -> list[MarketRecord]:
        records: list[MarketRecord] = []
        for row in frame.to_dict(orient="records"):
            symbol = normalize_symbol(row.get("代码"))
            title = clean_text(row.get("公告标题"))
            source_url = clean_text(row.get("网址"))
            event_time = _event_time(row.get("公告日期"), end_of_day=True)
            if (
                symbol is None
                or not title
                or event_time is None
                or event_time > data_cutoff
            ):
                continue
            payload = {
                "symbol": symbol,
                "short_name": clean_text(row.get("名称")),
                "title": title,
                "announcement_time": event_time.isoformat(),
                "announcement_type": clean_text(row.get("公告类型")),
                "source_url": source_url or None,
                "document_id": stable_hash(
                    {"symbol": symbol, "title": title, "url": source_url}
                )[:32],
                "full_text_status": "URL_ONLY",
                "batch_endpoint": "stock_notice_report",
            }
            record_id, digest = _raw_id(
                "raw_announcement",
                {
                    "source": "AKShare / Eastmoney notice batch",
                    "symbol": symbol,
                    "title": title,
                    "event_time": event_time,
                    "source_url": source_url,
                },
            )
            records.append(
                MarketRecord(
                    record_id=record_id,
                    symbol=symbol,
                    data_type=DataType.ANNOUNCEMENT,
                    event_time=event_time,
                    fetched_at=fetched_at,
                    source_name="AKShare / Eastmoney notice batch",
                    source_url=source_url or None,
                    source_level=SourceLevel.PUBLIC_WEB,
                    verified=False,
                    content_hash=digest,
                    data=payload,
                )
            )
            if len(records) >= limit:
                break
        return records

    def _announcements(
        self,
        request: DataExpansionRequest,
    ) -> tuple[list[dict[str, Any]], int, list[str]]:
        start = request.start_date or request.trade_date
        end = request.end_date or request.trade_date
        if start is None or end is None:
            raise ValueError("announcement expansion requires a date range")
        items: list[dict[str, Any]] = []
        request_count = 0
        current = start
        while current <= end and request_count < request.request_budget:
            batch_started_at = self.clock()
            try:
                result = self.batch_provider.fetch_announcements(current)
                request_count += result.request_count
                records = self._announcement_records(
                    result.frame,
                    fetched_at=self.clock(),
                    data_cutoff=request.data_cutoff,
                    limit=request.batch_size,
                )
                cluster_ids: list[str] = []
                if not request.dry_run:
                    self.repository.save_raw_records(records)
                    clusters = EventClusterService().cluster(records) if records else []
                    cluster_ids = [item.event_cluster_id for item in clusters]
                items.append(
                    {
                        "item_key": current.isoformat(),
                        "data_type": DataType.ANNOUNCEMENT.value,
                        "status": "SUCCESS" if records else "SUCCESS_EMPTY",
                        "source_record_ids": [item.record_id for item in records],
                        "event_cluster_ids": cluster_ids,
                        "request_count": result.request_count,
                        "processed_at": self.clock(),
                        "collection_audit": {
                            "data_kind": "ANNOUNCEMENT",
                            "provider": result.provider,
                            "window_start": datetime.combine(
                                current,
                                time.min,
                                tzinfo=SHANGHAI_TZ,
                            ),
                            "window_end": datetime.combine(
                                current,
                                time.max,
                                tzinfo=SHANGHAI_TZ,
                            ),
                            "provider_window_capability": result.capability,
                            "batch_key": current.isoformat(),
                            "status": (
                                "SUCCESS" if records else "SUCCESS_EMPTY"
                            ),
                            "result_count": len(records),
                            "full_text_count": sum(
                                bool(item.data.get("content"))
                                for item in records
                            ),
                            "metadata_only_count": sum(
                                not bool(item.data.get("content"))
                                for item in records
                            ),
                            "duplicate_source_count": (
                                len(records)
                                - len({item.record_id for item in records})
                            ),
                            "started_at": batch_started_at,
                            "completed_at": self.clock(),
                            "payload": {
                                "provider_metadata": result.metadata,
                                "success_empty_is_failure": False,
                            },
                        },
                    }
                )
                self.capabilities.record(
                    provider=result.provider,
                    capability=result.capability,
                    available=True,
                    verified_at=self.clock(),
                    batch_supported=True,
                    maximum_batch_size=max(1, len(result.frame)),
                    metadata=result.metadata,
                )
            except Exception as exc:
                request_count += 1
                items.append(
                    {
                        "item_key": current.isoformat(),
                        "data_type": DataType.ANNOUNCEMENT.value,
                        "status": "FAILED",
                        "request_count": 1,
                        "processed_at": self.clock(),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],
                        "collection_audit": {
                            "data_kind": "ANNOUNCEMENT",
                            "provider": request.provider,
                            "window_start": datetime.combine(
                                current,
                                time.min,
                                tzinfo=SHANGHAI_TZ,
                            ),
                            "window_end": datetime.combine(
                                current,
                                time.max,
                                tzinfo=SHANGHAI_TZ,
                            ),
                            "provider_window_capability": (
                                "BATCH_ANNOUNCEMENTS_BY_DATE"
                            ),
                            "batch_key": current.isoformat(),
                            "status": "FAILED",
                            "result_count": 0,
                            "full_text_count": 0,
                            "metadata_only_count": 0,
                            "duplicate_source_count": 0,
                            "started_at": batch_started_at,
                            "completed_at": self.clock(),
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:500],
                            "payload": {},
                        },
                    }
                )
            current += timedelta(days=1)
        warnings = []
        expected_days = (end - start).days + 1
        if request_count < expected_days:
            warnings.append("date range was truncated by request_budget")
        if request.batch_size < 1000:
            warnings.append("per-date records were capped by batch_size")
        return items, request_count, warnings

    def _news_records(
        self,
        frame: pd.DataFrame,
        *,
        fetched_at: datetime,
        data_cutoff: datetime,
        start_date: date | None,
        end_date: date | None,
        limit: int,
    ) -> list[MarketRecord]:
        records: list[MarketRecord] = []
        for row in frame.to_dict(orient="records"):
            title = clean_text(row.get("标题"))
            summary = clean_text(row.get("摘要"))
            source_url = clean_text(row.get("链接"))
            event_time = _event_time(row.get("发布时间"))
            if not title or event_time is None or event_time > data_cutoff:
                continue
            if start_date and event_time.date() < start_date:
                continue
            if end_date and event_time.date() > end_date:
                continue
            payload = {
                "title": title,
                "content": summary,
                "published_at": event_time.isoformat(),
                "url": source_url or None,
                "symbols": [],
                "full_text_status": "SUMMARY_ONLY" if summary else "TITLE_ONLY",
                "batch_endpoint": "stock_info_global_em",
            }
            record_id, digest = _raw_id(
                "raw_finance_news",
                {
                    "source": "AKShare / Eastmoney global finance news",
                    "title": title,
                    "event_time": event_time,
                    "source_url": source_url,
                },
            )
            records.append(
                MarketRecord(
                    record_id=record_id,
                    symbol="MARKET.GLOBAL",
                    data_type=DataType.FINANCE_NEWS,
                    event_time=event_time,
                    fetched_at=fetched_at,
                    source_name="AKShare / Eastmoney global finance news",
                    source_url=source_url or None,
                    source_level=SourceLevel.MEDIA,
                    verified=False,
                    content_hash=digest,
                    data=payload,
                )
            )
            if len(records) >= limit:
                break
        return records

    def _news_once(
        self,
        request: DataExpansionRequest,
    ) -> tuple[list[dict[str, Any]], int, list[str]]:
        if request.request_budget < 1:
            return [], 0, ["news fetch skipped by request_budget"]
        batch_started_at = self.clock()
        result = self.batch_provider.fetch_global_finance_news()
        records = self._news_records(
            result.frame,
            fetched_at=self.clock(),
            data_cutoff=request.data_cutoff,
            start_date=request.start_date,
            end_date=request.end_date,
            limit=request.batch_size,
        )
        cluster_ids: list[str] = []
        if not request.dry_run:
            self.repository.save_raw_records(records)
            clusters = EventClusterService().cluster(records) if records else []
            cluster_ids = [item.event_cluster_id for item in clusters]
        self.capabilities.record(
            provider=result.provider,
            capability=result.capability,
            available=True,
            verified_at=self.clock(),
            batch_supported=True,
            maximum_batch_size=max(1, len(result.frame)),
            metadata=result.metadata,
        )
        item = {
            "item_key": "latest",
            "data_type": DataType.FINANCE_NEWS.value,
            "status": "SUCCESS" if records else "SUCCESS_EMPTY",
            "source_record_ids": [record.record_id for record in records],
            "event_cluster_ids": cluster_ids,
            "request_count": result.request_count,
            "processed_at": self.clock(),
            "collection_audit": {
                "data_kind": "FINANCE_NEWS",
                "provider": result.provider,
                "window_start": min(
                    (record.event_time for record in records),
                    default=batch_started_at,
                ),
                "window_end": max(
                    (record.event_time for record in records),
                    default=self.clock(),
                ),
                "provider_window_capability": (
                    "LATEST_BATCH_NOT_GUARANTEED_ARCHIVE"
                ),
                "batch_key": "latest",
                "status": "SUCCESS" if records else "SUCCESS_EMPTY",
                "result_count": len(records),
                "full_text_count": sum(
                    record.data.get("full_text_status") == "FULL_TEXT"
                    for record in records
                ),
                "metadata_only_count": sum(
                    record.data.get("full_text_status") != "FULL_TEXT"
                    for record in records
                ),
                "duplicate_source_count": (
                    len(records)
                    - len({record.record_id for record in records})
                ),
                "started_at": batch_started_at,
                "completed_at": self.clock(),
                "payload": {
                    "provider_metadata": result.metadata,
                    "requested_start_date": request.start_date,
                    "requested_end_date": request.end_date,
                    "complete_30_day_archive": False,
                },
            },
        }
        return [
            item
        ], result.request_count, [
            "provider exposes only a latest-news batch, not a guaranteed 30-day archive"
        ]

    def _news(
        self,
        request: DataExpansionRequest,
    ) -> tuple[list[dict[str, Any]], int, list[str]]:
        if request.request_budget < 1:
            return [], 0, ["news fetch skipped by request_budget"]
        started_at = self.clock()
        try:
            return self._news_once(request)
        except Exception as exc:
            window_start = datetime.combine(
                request.start_date or request.data_cutoff.date(),
                time.min,
                tzinfo=SHANGHAI_TZ,
            )
            window_end = datetime.combine(
                request.end_date or request.data_cutoff.date(),
                time.max,
                tzinfo=SHANGHAI_TZ,
            )
            item = {
                "item_key": "latest",
                "data_type": DataType.FINANCE_NEWS.value,
                "status": "FAILED",
                "request_count": 1,
                "processed_at": self.clock(),
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:500],
                "collection_audit": {
                    "data_kind": "FINANCE_NEWS",
                    "provider": request.provider,
                    "window_start": window_start,
                    "window_end": window_end,
                    "provider_window_capability": (
                        "LATEST_BATCH_NOT_GUARANTEED_ARCHIVE"
                    ),
                    "batch_key": "latest",
                    "status": "FAILED",
                    "result_count": 0,
                    "full_text_count": 0,
                    "metadata_only_count": 0,
                    "duplicate_source_count": 0,
                    "started_at": started_at,
                    "completed_at": self.clock(),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:500],
                    "payload": {"complete_30_day_archive": False},
                },
            }
            return [item], 1, [
                "provider exposes only a latest-news batch, not a guaranteed "
                "30-day archive"
            ]

    def run(self, request: DataExpansionRequest) -> DataExpansionResponse:
        started_at = self.clock()
        started = perf_counter()
        run_id = f"expand_{stable_hash({'request': request.model_dump(mode='json'), 'started': started_at})[:24]}"
        if request.expansion_type == ExpansionType.DAILY_BARS:
            items, request_count, warnings = self._daily(request, run_id=run_id)
        elif request.expansion_type == ExpansionType.ANNOUNCEMENTS:
            items, request_count, warnings = self._announcements(request)
        elif request.expansion_type == ExpansionType.FINANCE_NEWS:
            items, request_count, warnings = self._news(request)
        else:
            raise ValueError(
                "ENTITY_LINKS and DAILY_UPDATE use their dedicated services"
            )
        success = sum(item["status"].startswith("SUCCESS") for item in items)
        skipped = sum(item["status"].startswith("SKIPPED") for item in items)
        failed = sum(item["status"] == "FAILED" for item in items)
        status = "SUCCESS" if failed == 0 else ("PARTIAL" if success else "FAILED")
        completed_at = self.clock()
        if not request.dry_run:
            self.repository.save_expansion_run(
                run_id=run_id,
                expansion_type=request.expansion_type.value,
                mode="APPLY",
                analysis_mode=request.analysis_mode.value,
                provider=request.provider,
                request_budget=request.request_budget,
                request_count=request_count,
                data_cutoff=request.data_cutoff,
                filters=request.model_dump(mode="json"),
                status=status,
                processed_count=len(items),
                success_count=success,
                skipped_count=skipped,
                conflict_count=0,
                failed_count=failed,
                started_at=started_at,
                completed_at=completed_at,
                report_path=request.report_path,
            )
            self.repository.save_expansion_items(run_id=run_id, items=items)
            audits: list[dict[str, Any]] = []
            for item in items:
                audit = item.get("collection_audit")
                if audit is None:
                    continue
                identity = {
                    "run_id": run_id,
                    "data_kind": audit["data_kind"],
                    "batch_key": audit["batch_key"],
                    "started_at": audit["started_at"],
                }
                audits.append(
                    {
                        **audit,
                        "audit_id": (
                            "collection_" + stable_hash(identity)[:32]
                        ),
                        "run_id": run_id,
                    }
                )
            self.repository.save_collection_window_audits(audits)
            self.capabilities.persist()
        return DataExpansionResponse(
            run_id=run_id,
            expansion_type=request.expansion_type,
            mode="DRY_RUN" if request.dry_run else "APPLY",
            analysis_mode=request.analysis_mode,
            provider=request.provider,
            request_budget=request.request_budget,
            request_count=request_count,
            processed_count=len(items),
            success_count=success,
            skipped_count=skipped,
            conflict_count=0,
            failed_count=failed,
            status=status,
            warnings=warnings,
            elapsed_seconds=max(0.0, perf_counter() - started),
        )


__all__ = ["DataExpansionService"]
