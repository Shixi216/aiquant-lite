from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from database.db import get_connection
from data_hub.repositories.full_market import FullMarketRepository
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.full_market import (
    CoverageMetric,
    DataCoverageResponse,
)
from data_hub.services.full_market_common import stable_hash
from data_hub.services.history_coverage_service import HistoryCoverageService


def _metric(numerator: int, denominator: int) -> CoverageMetric:
    return CoverageMetric(
        numerator=numerator,
        denominator=denominator,
        ratio=numerator / denominator if denominator else 0.0,
    )


class DataCoverageService:
    def __init__(
        self,
        *,
        repository: FullMarketRepository | None = None,
        history_coverage: HistoryCoverageService | None = None,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or FullMarketRepository()
        self.history_coverage = history_coverage or HistoryCoverageService(
            repository=HistoryRepository()
        )
        self.clock = clock or (lambda: datetime.now().astimezone())

    def generate(
        self,
        *,
        data_cutoff: datetime,
        persist: bool = True,
        report_path: str | None = None,
        snapshot_max_age_seconds: int = 300,
    ) -> DataCoverageResponse:
        version = self.repository.latest_universe_version(data_cutoff)
        universe_total = self.repository.universe_count(version=version)
        active_total = self.repository.universe_count(
            version=version,
            active_only=True,
        )
        history_report = self.history_coverage.generate(
            data_cutoff=data_cutoff
        )
        window_start = data_cutoff - timedelta(days=30)
        with get_connection() as connection:
            latest_snapshot = connection.execute(
                """
                SELECT snapshot_id, valid_symbol_count, snapshot_time
                FROM market_snapshot_runs
                WHERE mode = 'APPLY' AND completeness_status != 'FAILED'
                  AND data_cutoff <= ?
                ORDER BY snapshot_time DESC
                LIMIT 1
                """,
                [data_cutoff],
            ).fetchone()
            realtime_count = int(latest_snapshot[1]) if latest_snapshot else 0
            snapshot_time = latest_snapshot[2] if latest_snapshot else None
            legacy_history = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN count_days >= 20 THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN count_days >= 60 THEN 1 ELSE 0 END), 0)
                FROM (
                    SELECT symbol, COUNT(*) AS count_days
                    FROM canonical_market_records
                    WHERE data_type = 'daily_bar'
                      AND data_cutoff <= ?
                      AND verification_status != 'CONFLICT'
                    GROUP BY symbol
                )
                """,
                [data_cutoff],
            ).fetchone()
            turnover_count = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT symbol)
                    FROM (
                        SELECT symbol
                        FROM canonical_market_records
                        WHERE data_type = 'daily_bar'
                          AND data_cutoff <= ?
                          AND TRY_CAST(
                              json_extract(payload_json, '$.turnover_rate')
                              AS DOUBLE
                          ) IS NOT NULL
                        UNION
                        SELECT symbol
                        FROM market_snapshot_items
                        WHERE turnover_rate IS NOT NULL
                          AND snapshot_time <= ?
                    )
                    """,
                    [data_cutoff, data_cutoff],
                ).fetchone()[0]
            )
            industry_count = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT symbol)
                    FROM stock_industry_memberships
                    WHERE (valid_from IS NULL OR valid_from <= ?)
                      AND (valid_to IS NULL OR valid_to >= ?)
                    """,
                    [data_cutoff, data_cutoff],
                ).fetchone()[0]
            )
            event_coverages = {
                event_type: int(count)
                for event_type, count in connection.execute(
                    """
                    SELECT c.event_type, COUNT(DISTINCT l.symbol)
                    FROM event_clusters c
                    JOIN event_symbol_links l USING (event_cluster_id)
                    WHERE c.event_time BETWEEN ? AND ?
                    GROUP BY c.event_type
                    """,
                    [window_start, data_cutoff],
                ).fetchall()
            }
            collection_stats = {
                str(row[0]): {
                    "successful_batches": int(row[1]),
                    "failed_batches": int(row[2]),
                    "result_count": int(row[3]),
                    "full_text_count": int(row[4]),
                    "metadata_only_count": int(row[5]),
                    "duplicate_source_count": int(row[6]),
                }
                for row in connection.execute(
                    """
                    SELECT
                        data_kind,
                        COUNT(*) FILTER (
                            WHERE status IN ('SUCCESS', 'SUCCESS_EMPTY')
                        ),
                        COUNT(*) FILTER (WHERE status = 'FAILED'),
                        COALESCE(SUM(result_count), 0),
                        COALESCE(SUM(full_text_count), 0),
                        COALESCE(SUM(metadata_only_count), 0),
                        COALESCE(SUM(duplicate_source_count), 0)
                    FROM collection_window_audits
                    WHERE window_end >= ? AND window_start <= ?
                      AND completed_at <= ?
                    GROUP BY data_kind
                    """,
                    [window_start, data_cutoff, data_cutoff],
                ).fetchall()
            }
            event_record_stats = {
                str(row[0]): {
                    "event_count": int(row[1]),
                    "full_text_count": int(row[2]),
                    "metadata_only_count": int(row[3]),
                }
                for row in connection.execute(
                    """
                    SELECT
                        data_type,
                        COUNT(*),
                        COUNT(*) FILTER (
                            WHERE COALESCE(
                                json_extract_string(
                                    payload_json,
                                    '$.content'
                                ),
                                ''
                            ) != ''
                        ),
                        COUNT(*) FILTER (
                            WHERE COALESCE(
                                json_extract_string(
                                    payload_json,
                                    '$.content'
                                ),
                                ''
                            ) = ''
                        )
                    FROM data_records
                    WHERE data_type IN ('announcement', 'finance_news')
                      AND event_time BETWEEN ? AND ?
                      AND fetched_at <= ?
                    GROUP BY data_type
                    """,
                    [window_start, data_cutoff, data_cutoff],
                ).fetchall()
            }
            news_link_counts = connection.execute(
                """
                SELECT
                    COUNT(DISTINCT c.event_cluster_id) FILTER (
                        WHERE l.event_cluster_id IS NOT NULL
                    ),
                    COUNT(DISTINCT c.event_cluster_id) FILTER (
                        WHERE l.event_cluster_id IS NULL
                    ),
                    COUNT(DISTINCT l.symbol)
                FROM event_clusters c
                LEFT JOIN event_symbol_links l USING (event_cluster_id)
                WHERE c.event_type = 'finance_news'
                  AND c.event_time BETWEEN ? AND ?
                  AND c.data_cutoff <= ?
                """,
                [window_start, data_cutoff, data_cutoff],
            ).fetchone()
            factor_counts = {
                "sentiment": int(
                    connection.execute(
                        """
                        SELECT COUNT(DISTINCT symbol)
                        FROM sentiment_symbol_snapshots
                        WHERE data_cutoff <= ?
                        """,
                        [data_cutoff],
                    ).fetchone()[0]
                ),
                "policy_news": int(
                    connection.execute(
                        """
                        SELECT COUNT(DISTINCT symbol)
                        FROM policy_news_symbol_snapshots
                        WHERE data_cutoff <= ?
                        """,
                        [data_cutoff],
                    ).fetchone()[0]
                ),
                "capital_flow": int(
                    connection.execute(
                        """
                        SELECT COUNT(DISTINCT symbol)
                        FROM capital_flow_symbol_snapshots
                        WHERE data_cutoff <= ?
                        """,
                        [data_cutoff],
                    ).fetchone()[0]
                ),
            }
            total_events = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM event_clusters
                    WHERE data_cutoff <= ?
                    """,
                    [data_cutoff],
                ).fetchone()[0]
            )
            linked_events = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT c.event_cluster_id)
                    FROM event_clusters c
                    JOIN event_symbol_links l USING (event_cluster_id)
                    WHERE c.data_cutoff <= ?
                    """,
                    [data_cutoff],
                ).fetchone()[0]
            )
            full_text = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM data_records
                    WHERE data_type IN ('announcement', 'finance_news')
                      AND fetched_at <= ?
                      AND COALESCE(
                          json_extract_string(payload_json, '$.content'), ''
                      ) != ''
                    """,
                    [data_cutoff],
                ).fetchone()[0]
            )
            event_sources = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM data_records
                    WHERE data_type IN ('announcement', 'finance_news')
                      AND fetched_at <= ?
                    """,
                    [data_cutoff],
                ).fetchone()[0]
            )
            model_analyzed = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT event_cluster_id)
                    FROM (
                        SELECT event_cluster_id
                        FROM sentiment_event_analyses
                        WHERE generated_at <= ?
                        UNION
                        SELECT event_cluster_id
                        FROM policy_news_event_analyses
                        WHERE generated_at <= ?
                    )
                    """,
                    [data_cutoff, data_cutoff],
                ).fetchone()[0]
            )
            conflict_count = int(
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM canonical_market_records
                         WHERE verification_status = 'CONFLICT'
                           AND data_cutoff <= ?)
                        +
                        (SELECT COUNT(*) FROM canonical_financial_records
                         WHERE verification_status = 'CONFLICT'
                           AND data_cutoff <= ?)
                    """,
                    [data_cutoff, data_cutoff],
                ).fetchone()[0]
            )
            single_count = int(
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM canonical_market_records
                         WHERE verification_status = 'SINGLE_SOURCE'
                           AND data_cutoff <= ?)
                        +
                        (SELECT COUNT(*) FROM canonical_financial_records
                         WHERE verification_status = 'SINGLE_SOURCE'
                           AND data_cutoff <= ?)
                    """,
                    [data_cutoff, data_cutoff],
                ).fetchone()[0]
            )
            latest_updates: dict[str, datetime | None] = {}
            update_queries = {
                "universe": (
                    "SELECT MAX(completed_at) FROM universe_sync_runs "
                    "WHERE status = 'SUCCESS'"
                ),
                "market_snapshot": (
                    "SELECT MAX(completed_at) FROM market_snapshot_runs "
                    "WHERE completeness_status != 'FAILED'"
                ),
                "data_expansion": (
                    "SELECT MAX(completed_at) FROM data_expansion_runs "
                    "WHERE status IN ('SUCCESS', 'PARTIAL')"
                ),
            }
            for name, query in update_queries.items():
                latest_updates[name] = connection.execute(query).fetchone()[0]

        snapshot_stale = 0
        warnings: list[str] = []
        if snapshot_time is None:
            warnings.append("no persisted market snapshot")
        else:
            age = max(
                0.0,
                (
                    data_cutoff.astimezone(timezone.utc)
                    - snapshot_time.astimezone(timezone.utc)
                ).total_seconds(),
            )
            snapshot_stale = int(age > snapshot_max_age_seconds)
            if snapshot_stale:
                warnings.append("latest market snapshot is stale at data_cutoff")
        announcement_stats = collection_stats.get("ANNOUNCEMENT", {})
        news_stats = collection_stats.get("FINANCE_NEWS", {})
        announcement_records = event_record_stats.get("announcement", {})
        news_records = event_record_stats.get("finance_news", {})
        warnings.append(
            "finance news coverage is provider-window coverage; AKShare latest "
            "news is not a complete 30-day archive"
        )
        if not history_report.calendar_providers:
            warnings.append(
                "history coverage has no audited trading-calendar provider"
            )
        metrics = {
            "active_listing": _metric(active_total, universe_total),
            "realtime_snapshot": _metric(realtime_count, active_total),
            "history_20d": history_report.history_20d,
            "history_60d": history_report.history_60d,
            "history_20d_legacy": _metric(
                int(legacy_history[0]),
                active_total,
            ),
            "history_60d_legacy": _metric(
                int(legacy_history[1]),
                active_total,
            ),
            "turnover": _metric(turnover_count, active_total),
            "industry": _metric(industry_count, active_total),
            "announcements_30d": _metric(
                event_coverages.get("announcement", 0),
                active_total,
            ),
            "finance_news_30d_legacy": _metric(
                event_coverages.get("finance_news", 0),
                active_total,
            ),
            "finance_news_provider_window": _metric(
                event_coverages.get("finance_news", 0),
                active_total,
            ),
            "sentiment": _metric(factor_counts["sentiment"], active_total),
            "policy_news": _metric(factor_counts["policy_news"], active_total),
            "capital_flow": _metric(factor_counts["capital_flow"], active_total),
            "event_linking": _metric(linked_events, total_events),
            "full_text": _metric(full_text, event_sources),
            "model_analyzed_events": _metric(model_analyzed, total_events),
        }
        counts = {
            "universe_total": universe_total,
            "active_total": active_total,
            "unlinked_events": max(0, total_events - linked_events),
            "conflict_records": conflict_count,
            "single_source_records": single_count,
            "stale_snapshots": snapshot_stale,
            "history_eligible_20d": (
                history_report.eligibility.eligible_20d_count
            ),
            "history_covered_20d": history_report.history_20d.numerator,
            "history_eligible_60d": (
                history_report.eligibility.eligible_60d_count
            ),
            "history_covered_60d": history_report.history_60d.numerator,
            "history_newly_listed_under_20d": (
                history_report.eligibility.newly_listed_20d_count
            ),
            "history_newly_listed_under_60d": (
                history_report.eligibility.newly_listed_60d_count
            ),
            "history_long_suspended": (
                history_report.eligibility.long_suspended_count
            ),
            "history_failed_collection": (
                history_report.eligibility.failed_collection_count
            ),
            "announcement_batches_success": int(
                announcement_stats.get("successful_batches", 0)
            ),
            "announcement_batches_failed": int(
                announcement_stats.get("failed_batches", 0)
            ),
            "announcement_events": int(
                announcement_records.get("event_count", 0)
            ),
            "announcement_stocks": event_coverages.get("announcement", 0),
            "announcement_full_text": int(
                announcement_records.get("full_text_count", 0)
            ),
            "announcement_metadata_only": int(
                announcement_records.get("metadata_only_count", 0)
            ),
            "news_batches_success": int(
                news_stats.get("successful_batches", 0)
            ),
            "news_batches_failed": int(
                news_stats.get("failed_batches", 0)
            ),
            "news_events": int(news_records.get("event_count", 0)),
            "news_linked": int(news_link_counts[0]),
            "news_unlinked": int(news_link_counts[1]),
            "news_stocks": int(news_link_counts[2]),
            "news_full_text": int(news_records.get("full_text_count", 0)),
            "news_metadata_only": int(
                news_records.get("metadata_only_count", 0)
            ),
            "news_duplicate_sources": int(
                news_stats.get("duplicate_source_count", 0)
            ),
        }
        content = {
            "data_cutoff": data_cutoff,
            "universe_version": version,
            "metrics": {
                key: value.model_dump(mode="json")
                for key, value in metrics.items()
            },
            "counts": counts,
            "latest_successful_updates": latest_updates,
            "warnings": warnings,
        }
        content_hash = stable_hash(content)
        generated_at = self.clock()
        report = DataCoverageResponse(
            coverage_id=f"coverage_{content_hash[:32]}",
            data_cutoff=data_cutoff,
            generated_at=generated_at,
            universe_version=version,
            metrics=metrics,
            counts=counts,
            latest_successful_updates=latest_updates,
            warnings=warnings,
            content_hash=content_hash,
        )
        if persist:
            self.repository.save_coverage(report, report_path)
        return report


__all__ = ["DataCoverageService"]
