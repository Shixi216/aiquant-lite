from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from data_hub.schemas.unified import FactorType
from trading.research.orchestration.models import EVIDENCE_GRAPH_VERSION
from trading.research.orchestration.schemas import (
    FactorView,
    SharedEvidenceGroup,
)


def _stable_id(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def event_cluster_ids(factor: FactorView) -> set[str]:
    return {
        *factor.event_cluster_ids,
        *(item for item in factor.evidence_ids if item.startswith("evt_")),
    }


def market_record_ids(factor: FactorView) -> set[str]:
    return {
        *factor.market_record_ids,
        *(
            item
            for item in factor.evidence_ids
            if item.startswith(("hbar_", "bar_", "cmr_"))
        ),
    }


def build_evidence_graph(
    factors: dict[FactorType, FactorView],
) -> tuple[list[SharedEvidenceGroup], list[str]]:
    by_evidence: dict[str, set[FactorType]] = defaultdict(set)
    for factor_type, factor in factors.items():
        for evidence_id in factor.evidence_ids:
            by_evidence[evidence_id].add(factor_type)

    groups: list[SharedEvidenceGroup] = []
    covered: set[tuple[str, tuple[FactorType, ...]]] = set()
    for evidence_id, factor_types in sorted(by_evidence.items()):
        if len(factor_types) < 2:
            continue
        ordered = tuple(sorted(factor_types, key=lambda item: item.value))
        kind = (
            "EVENT_CLUSTER"
            if evidence_id.startswith("evt_")
            else "MARKET_RECORD"
            if evidence_id.startswith(("hbar_", "bar_", "cmr_"))
            else "DATA_RECORD"
        )
        covered.add((evidence_id, ordered))
        groups.append(
            SharedEvidenceGroup(
                group_id="seg_"
                + _stable_id(
                    {
                        "evidence_id": evidence_id,
                        "factor_types": [
                            factor_type.value for factor_type in ordered
                        ],
                        "version": EVIDENCE_GRAPH_VERSION,
                    }
                )[:32],
                evidence_kind=kind,
                evidence_ids=[evidence_id],
                factor_types=list(ordered),
                algorithm_version=EVIDENCE_GRAPH_VERSION,
            )
        )

    sentiment = factors.get(FactorType.SENTIMENT)
    policy = factors.get(FactorType.POLICY_NEWS)
    shared_clusters: list[str] = []
    if sentiment is not None and policy is not None:
        shared_clusters = sorted(
            event_cluster_ids(sentiment) & event_cluster_ids(policy)
        )
        for cluster_id in shared_clusters:
            ordered = (FactorType.POLICY_NEWS, FactorType.SENTIMENT)
            key = (cluster_id, ordered)
            if key in covered:
                continue
            groups.append(
                SharedEvidenceGroup(
                    group_id="seg_"
                    + _stable_id(
                        {
                            "event_cluster_id": cluster_id,
                            "factor_types": [
                                factor_type.value for factor_type in ordered
                            ],
                            "version": EVIDENCE_GRAPH_VERSION,
                        }
                    )[:32],
                    evidence_kind="EVENT_CLUSTER",
                    evidence_ids=[cluster_id],
                    factor_types=list(ordered),
                    algorithm_version=EVIDENCE_GRAPH_VERSION,
                )
            )
    groups.sort(key=lambda item: item.group_id)
    return groups, shared_clusters


__all__ = [
    "build_evidence_graph",
    "event_cluster_ids",
    "market_record_ids",
]
