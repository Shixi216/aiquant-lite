"""Dashboard aggregation: server-side breadth + five-field time semantics.

The five time fields each take their value from a DIFFERENT upstream field —
structurally preventing any generated_at/snapshot_time from impersonating a
trade date.
"""
from __future__ import annotations

import asyncio

import httpx

from webui import canonical
from webui.services.blocks import block
from webui.services.provenance import provenance

TIME_SEMANTIC_SOURCES = {
    "market_trade_date": "router.market_breadth.market_trade_date",
    "market_snapshot_cutoff": "router.market_breadth.snapshot_time",
    "score_data_cutoff": "router.cross_section.data_cutoff",
    "score_generated_at": "router.cross_section.generated_at",
    "coverage_report_date": "router.data_coverage.data_cutoff",
}


def _time_field(value, source_key: str) -> dict:
    return {
        "value": value,
        "source": TIME_SEMANTIC_SOURCES[source_key],
        "label_zh": source_key,
    }


def _component_chips(
    health: dict | None,
    providers_result,
    coverage_result,
) -> list[dict]:
    chips: list[dict] = []
    router_status = (health or {}).get("status", "unreachable")
    chips.append(
        {
            "name": "router",
            "status": router_status,
            "healthy": router_status == "ok",
        }
    )
    open_breakers: dict[str, str] = {}
    provider_states: dict[str, str] = {}
    if providers_result is not None and providers_result.ok:
        for item in (providers_result.data or {}).get("providers", []) or []:
            name = str(
                item.get("provider") or item.get("name") or item.get("source")
                or "unknown"
            ).casefold()
            state = str(item.get("state", "unknown")).casefold()
            provider_states[name] = state
            if state == "open":
                open_breakers[name] = state
    for name, label in (
        ("data_hub", "data_hub"),
        ("jinniu", "jinniu"),
        ("tushare", "tushare"),
        ("baostock", "baostock"),
    ):
        state = provider_states.get(name, "unknown")
        chips.append(
            {
                "name": label,
                "status": state,
                # only a live open breaker is a hard failure; unknown is neutral
                "healthy": state != "open",
            }
        )
    coverage_ok = coverage_result is not None and coverage_result.ok
    chips.append(
        {
            "name": "coverage",
            "status": "ok" if coverage_ok else "unreachable",
            "healthy": coverage_ok,
        }
    )
    return chips


async def dashboard(client: httpx.AsyncClient) -> dict:
    (
        breadth_result,
        coverage_result,
        providers_result,
        cross_result,
        health_result,
        context_result,
        dragon_tiger_result,
    ) = await asyncio.gather(
        # Daily full-market snapshots remain current across non-trading days.
        # The explicit market_trade_date stays visible in the UI.
        canonical.market_breadth(client, maximum_age_seconds=172_800),
        canonical.data_coverage(client),
        canonical.providers_health(client),
        canonical.cross_section_latest(client, limit=1),
        canonical.router_health(client),
        canonical.equity_context_overview(client),
        canonical.dragon_tiger_board(client),
        return_exceptions=True,
    )

    def _or_none(result):
        return None if isinstance(result, BaseException) or not result.ok else result

    breadth = _or_none(breadth_result)
    coverage = _or_none(coverage_result)
    cross = _or_none(cross_result)
    health = None if isinstance(health_result, BaseException) else (
        health_result.data if health_result.ok else None
    )
    providers = _or_none(providers_result)
    context = _or_none(context_result)
    dragon_tiger = _or_none(dragon_tiger_result)

    breadth_data = (breadth.data or {}) if breadth else None
    if breadth_data:
        breadth_block = block(
            breadth_data,
            stale=bool(breadth_data.get("stale")),
            source="router.market_breadth",
        )
        breadth_block["formal_status"] = (
            "auxiliary" if breadth_data.get("partial_universe") else "formal"
        )
    else:
        reason = (
            breadth_result.reason_code
            if not isinstance(breadth_result, BaseException)
            else "router_unreachable"
        )
        breadth_block = block(reason_code=reason, source="router.market_breadth")

    cross_data = (cross.data or {}) if cross else None
    score_time_source = cross_data.get("data_cutoff") if cross_data else None
    score_generated = cross_data.get("generated_at") if cross_data else None

    coverage_data = (coverage.data or {}) if coverage else None

    if cross_data:
        score_preview_block = block(cross_data, source="router.cross_section")
    else:
        score_preview_block = block(
            reason_code=(
                cross_result.reason_code
                if not isinstance(cross_result, BaseException)
                else "router_unreachable"
            ),
            source="router.cross_section",
        )
    if coverage_data:
        coverage_block = block(
            coverage_data,
            stale=bool(coverage_data.get("stale")),
            source="router.data_coverage",
        )
    else:
        coverage_block = block(
            reason_code=(
                coverage_result.reason_code
                if not isinstance(coverage_result, BaseException)
                else "router_unreachable"
            ),
            source="router.data_coverage",
        )

    time_semantics = {
        "market_trade_date": _time_field(
            breadth_data.get("market_trade_date") if breadth_data else None,
            "market_trade_date",
        ),
        "market_snapshot_cutoff": _time_field(
            breadth_data.get("snapshot_time") if breadth_data else None,
            "market_snapshot_cutoff",
        ),
        "score_data_cutoff": _time_field(score_time_source, "score_data_cutoff"),
        "score_generated_at": _time_field(score_generated, "score_generated_at"),
        "coverage_report_date": _time_field(
            coverage_data.get("data_cutoff") if coverage_data else None,
            "coverage_report_date",
        ),
    }

    return {
        "time_semantics": time_semantics,
        "market_breadth": breadth_block,
        "score_preview": score_preview_block,
        "coverage": coverage_block,
        "components": _component_chips(health, providers, coverage_result),
        "market_context": (
            block(context.data, source="router.equity_context")
            if context else block(
                reason_code=(context_result.reason_code if not isinstance(context_result, BaseException) else "router_unreachable"),
                source="router.equity_context",
            )
        ),
        "dragon_tiger": (
            block(dragon_tiger.data, source="router.sentiment_v3_dragon_tiger")
            if dragon_tiger else block(
                reason_code=(dragon_tiger_result.reason_code if not isinstance(dragon_tiger_result, BaseException) else "router_unreachable"),
                source="router.sentiment_v3_dragon_tiger",
            )
        ),
        "provenance": provenance(
            data_source="router_canonical",
            calculation_source="router_canonical",
            formal_status="formal" if breadth_data else "unavailable",
        ),
    }


__all__ = ["dashboard", "TIME_SEMANTIC_SOURCES"]
