from __future__ import annotations

import re
from datetime import datetime
from time import perf_counter
from typing import Any

from data_hub.repositories.full_market import FullMarketRepository
from data_hub.schemas.full_market import (
    AliasType,
    DataExpansionRequest,
    DataExpansionResponse,
    ExpansionType,
)
from data_hub.services.full_market_common import (
    normalize_alias,
    normalize_symbol,
    stable_hash,
)


ENTITY_LINK_MAPPING_VERSION = "entity-link-rules-v1"
_CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


class EntityLinkingService:
    """Deterministic entity links; ambiguous names stay unlinked."""

    def __init__(
        self,
        *,
        repository: FullMarketRepository | None = None,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or FullMarketRepository()
        self.clock = clock or (lambda: datetime.now().astimezone())

    @staticmethod
    def _audit(
        *,
        event: dict[str, Any],
        symbol: str | None,
        link_type: str,
        matched_text: str | None,
        confidence: float,
        rule: str,
        status: str,
        generated_at: datetime,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = {
            "event_cluster_id": event["event_cluster_id"],
            "symbol": symbol,
            "link_type": link_type,
            "matched_text": matched_text,
            "rule": rule,
            "status": status,
            "mapping_version": ENTITY_LINK_MAPPING_VERSION,
        }
        return {
            "audit_id": f"link_{stable_hash(identity)[:32]}",
            "event_cluster_id": event["event_cluster_id"],
            "symbol": symbol,
            "link_type": link_type,
            "matched_text": matched_text,
            "relevance_weight": confidence if symbol else 0.0,
            "confidence": confidence,
            "evidence_ids": [event["primary_source_id"]],
            "matching_rule": rule,
            "mapping_version": ENTITY_LINK_MAPPING_VERSION,
            "generated_at": generated_at,
            "manually_confirmed": False,
            "status": status,
            "payload": payload or {},
        }

    def build(
        self,
        request: DataExpansionRequest,
        *,
        limit: int | None = None,
    ) -> DataExpansionResponse:
        if request.expansion_type != ExpansionType.ENTITY_LINKS:
            raise ValueError("entity linker requires ENTITY_LINKS expansion type")
        started_at = self.clock()
        started = perf_counter()
        events = self.repository.event_link_inputs(
            data_cutoff=request.data_cutoff,
            limit=limit,
        )
        version = self.repository.latest_universe_version(request.data_cutoff)
        _, _, stocks = self.repository.list_universe(
            version=version,
            limit=10_000,
        )
        symbols = {item.symbol for item in stocks}
        aliases = self.repository.aliases(
            version=version,
            data_cutoff=request.data_cutoff,
        )
        aliases_by_text: dict[str, list[Any]] = {}
        alias_prefix_index: dict[str, set[str]] = {}
        for alias in aliases:
            normalized = alias.normalized_alias
            aliases_by_text.setdefault(normalized, []).append(alias)
            if len(normalized) >= 4:
                alias_prefix_index.setdefault(normalized[:4], set()).add(normalized)

        audits: list[dict[str, Any]] = []
        linked_events = 0
        ambiguous_events = 0
        for event in events:
            payload = event["payload"]
            text = " ".join(
                str(value)
                for value in (
                    event["title"],
                    payload.get("title"),
                    payload.get("content"),
                    payload.get("summary"),
                )
                if value
            )
            payload_symbol = normalize_symbol(
                payload.get("symbol") or payload.get("raw_code")
            )
            if payload_symbol in symbols:
                audits.append(
                    self._audit(
                        event=event,
                        symbol=payload_symbol,
                        link_type="SOURCE_SYMBOL",
                        matched_text=str(
                            payload.get("symbol") or payload.get("raw_code")
                        ),
                        confidence=1.0,
                        rule="SOURCE_PAYLOAD_SYMBOL",
                        status="LINKED",
                        generated_at=started_at,
                    )
                )
                linked_events += 1
                continue

            code_matches: set[str] = set()
            for code in _CODE.findall(text):
                for suffix in ("SH", "SZ", "BJ"):
                    candidate = f"{code}.{suffix}"
                    if candidate in symbols:
                        code_matches.add(candidate)
            if len(code_matches) == 1:
                symbol = next(iter(code_matches))
                audits.append(
                    self._audit(
                        event=event,
                        symbol=symbol,
                        link_type="TEXT_SYMBOL",
                        matched_text=symbol.split(".", 1)[0],
                        confidence=0.99,
                        rule="EXPLICIT_TEXT_CODE",
                        status="LINKED",
                        generated_at=started_at,
                    )
                )
                linked_events += 1
                continue
            if len(code_matches) > 1:
                audits.append(
                    self._audit(
                        event=event,
                        symbol=None,
                        link_type="AMBIGUOUS_CODES",
                        matched_text=",".join(sorted(code_matches)),
                        confidence=0.0,
                        rule="MULTIPLE_EXPLICIT_CODES",
                        status="UNLINKED_AMBIGUOUS",
                        generated_at=started_at,
                    )
                )
                ambiguous_events += 1
                continue

            normalized_text = normalize_alias(text)
            candidates: list[tuple[Any, float, str]] = []
            candidate_names: set[str] = set()
            for index in range(max(0, len(normalized_text) - 3)):
                candidate_names.update(
                    alias_prefix_index.get(normalized_text[index : index + 4], ())
                )
            for normalized_name in candidate_names:
                if normalized_name not in normalized_text:
                    continue
                values = aliases_by_text[normalized_name]
                for alias in values:
                    if alias.alias_type == AliasType.COMPANY_FULL_NAME:
                        candidates.append((alias, 0.98, "EXACT_FULL_NAME"))
                    elif (
                        alias.alias_type
                        in {
                            AliasType.CURRENT_SHORT_NAME,
                            AliasType.HISTORICAL_SHORT_NAME,
                        }
                        and len(normalized_name) >= 4
                    ):
                        candidates.append((alias, 0.90, "VALID_ALIAS_EXACT"))
            candidate_symbols = {item[0].symbol for item in candidates}
            if len(candidate_symbols) == 1:
                best = max(candidates, key=lambda item: item[1])
                audits.append(
                    self._audit(
                        event=event,
                        symbol=best[0].symbol,
                        link_type=best[0].alias_type.value,
                        matched_text=best[0].alias_name,
                        confidence=best[1],
                        rule=best[2],
                        status="LINKED",
                        generated_at=started_at,
                    )
                )
                linked_events += 1
            elif candidate_symbols:
                audits.append(
                    self._audit(
                        event=event,
                        symbol=None,
                        link_type="AMBIGUOUS_ALIAS",
                        matched_text=None,
                        confidence=0.0,
                        rule="ALIAS_COLLISION",
                        status="UNLINKED_AMBIGUOUS",
                        generated_at=started_at,
                        payload={"candidate_symbols": sorted(candidate_symbols)},
                    )
                )
                ambiguous_events += 1
            else:
                audits.append(
                    self._audit(
                        event=event,
                        symbol=None,
                        link_type="NO_RELIABLE_MATCH",
                        matched_text=None,
                        confidence=0.0,
                        rule="NO_RULE_MATCH",
                        status="UNLINKED",
                        generated_at=started_at,
                    )
                )

        completed_at = self.clock()
        run_id = f"entity_{stable_hash({'cutoff': request.data_cutoff, 'version': version})[:24]}"
        if not request.dry_run:
            self.repository.save_entity_links(audits)
            self.repository.save_expansion_run(
                run_id=run_id,
                expansion_type=ExpansionType.ENTITY_LINKS.value,
                mode="APPLY",
                analysis_mode=request.analysis_mode.value,
                provider="LOCAL_RULES",
                request_budget=request.request_budget,
                request_count=0,
                data_cutoff=request.data_cutoff,
                filters=request.model_dump(mode="json"),
                status="SUCCESS",
                processed_count=len(events),
                success_count=linked_events,
                skipped_count=len(events) - linked_events,
                conflict_count=ambiguous_events,
                failed_count=0,
                started_at=started_at,
                completed_at=completed_at,
                report_path=request.report_path,
            )
        return DataExpansionResponse(
            run_id=run_id,
            expansion_type=ExpansionType.ENTITY_LINKS,
            mode="DRY_RUN" if request.dry_run else "APPLY",
            analysis_mode=request.analysis_mode,
            provider="LOCAL_RULES",
            request_budget=request.request_budget,
            request_count=0,
            processed_count=len(events),
            success_count=linked_events,
            skipped_count=len(events) - linked_events,
            conflict_count=ambiguous_events,
            failed_count=0,
            status="SUCCESS",
            warnings=[],
            elapsed_seconds=max(0.0, perf_counter() - started),
        )

    def reverse(
        self,
        *,
        event_cluster_id: str,
        symbol: str,
        supersedes_audit_id: str,
        reason: str,
    ) -> str:
        generated_at = self.clock()
        identity = {
            "event_cluster_id": event_cluster_id,
            "symbol": symbol,
            "supersedes": supersedes_audit_id,
            "reason": reason,
            "generated_at": generated_at,
        }
        audit_id = f"link_reverse_{stable_hash(identity)[:32]}"
        self.repository.reverse_entity_link(
            {
                "audit_id": audit_id,
                "event_cluster_id": event_cluster_id,
                "symbol": symbol,
                "link_type": "MANUAL_REVERSAL",
                "matched_text": None,
                "relevance_weight": 0.0,
                "confidence": 1.0,
                "evidence_ids": [],
                "matching_rule": "MANUAL_REVERSAL",
                "mapping_version": ENTITY_LINK_MAPPING_VERSION,
                "generated_at": generated_at,
                "supersedes_audit_id": supersedes_audit_id,
                "payload": {"reason": reason},
            }
        )
        return audit_id


__all__ = ["ENTITY_LINK_MAPPING_VERSION", "EntityLinkingService"]
