from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Iterable

from database.db import get_connection


BUY_ACTION = "buy"
HORIZONS = (1, 3, 5, 10, 20)


def _drawdown(values: list[tuple[Any, float]]) -> float | None:
    if not values:
        return None
    by_day: dict[Any, list[float]] = defaultdict(list)
    for day, value in values:
        by_day[day].append(value)
    equity = peak = 1.0
    maximum = 0.0
    for day in sorted(by_day):
        equity *= 1 + statistics.fmean(by_day[day])
        peak = max(peak, equity)
        maximum = min(maximum, equity / peak - 1)
    return maximum


def _metrics(
    observation_ids: set[str],
    observations: dict[str, dict[str, Any]],
    labels: dict[tuple[str, int], dict[str, float]],
    horizon: int,
) -> dict[str, Any]:
    values = [
        (observations[item]["trade_date"], labels[(item, horizon)])
        for item in observation_ids
        if (item, horizon) in labels
    ]
    returns = [item[1]["return"] for item in values]
    if not returns:
        return {
            "sample_count": 0,
            "average_return": None,
            "median_return": None,
            "win_rate": None,
            "average_mfe": None,
            "average_mae": None,
            "research_cohort_max_drawdown": None,
        }
    return {
        "sample_count": len(returns),
        "average_return": statistics.fmean(returns),
        "median_return": statistics.median(returns),
        "win_rate": sum(value > 0 for value in returns) / len(returns),
        "average_mfe": statistics.fmean(item[1]["mfe"] for item in values),
        "average_mae": statistics.fmean(item[1]["mae"] for item in values),
        "research_cohort_max_drawdown": _drawdown(
            [(day, item["return"]) for day, item in values]
        ),
    }


def summarize_rows(
    observation_rows: Iterable[dict[str, Any]],
    label_rows: Iterable[dict[str, Any]],
    *,
    _include_weekly: bool = True,
) -> dict[str, Any]:
    observation_rows = list(observation_rows)
    label_rows = list(label_rows)
    observations = {row["observation_id"]: row for row in observation_rows}
    labels = {
        (row["observation_id"], int(row["horizon"])): row
        for row in label_rows
    }
    dates = {row["trade_date"] for row in observations.values()}
    a_buy = {
        key for key, row in observations.items()
        if row["a_action"] == BUY_ACTION
    }
    b_buy = {
        key for key, row in observations.items()
        if row["b_action"] == BUY_ACTION
    }
    ranked = {
        "A": sorted(
            a_buy,
            key=lambda key: observations[key]["a_score"],
            reverse=True,
        ),
        "B": sorted(
            b_buy,
            key=lambda key: observations[key]["b_score"],
            reverse=True,
        ),
    }
    groups: dict[str, Any] = {}
    for name, items in ranked.items():
        scopes = {"all_buy": set(items)}
        for fraction in (0.10, 0.20, 0.30):
            count = 0 if not items else max(1, round(len(items) * fraction))
            scopes[f"top_{round(fraction * 100)}"] = set(items[:count])
        groups[name] = {
            scope: {
                str(horizon): _metrics(
                    ids, observations, labels, horizon,
                )
                for horizon in HORIZONS
            }
            for scope, ids in scopes.items()
        }
    a_twenty = sorted(
        (
            (key, labels[(key, 20)]["return"])
            for key in a_buy
            if (key, 20) in labels
        ),
        key=lambda item: item[1],
    )
    tail = 0 if not a_twenty else max(1, round(len(a_twenty) * 0.10))
    losers = {key for key, _ in a_twenty[:tail]}
    winners = {key for key, _ in a_twenty[-tail:]} if tail else set()
    result = {
        "status": "READY" if len(dates) >= 20 else "COLLECTING",
        "minimum_ready_trade_days": 20,
        "observed_trade_days": len(dates),
        "observation_count": len(observations),
        "a_buy_signal_count": len(a_buy),
        "b_buy_signal_count": len(b_buy),
        "action_divergence_count": sum(
            row["a_action"] != row["b_action"]
            for row in observations.values()
        ),
        "b_blocked_overheated_a_buys": len(a_buy - b_buy),
        "b_missed_top_10_winner_count": len(winners - b_buy),
        "b_avoided_bottom_10_loser_count": len(losers - b_buy),
        "top_10_winner_retention": (
            None if not winners else len(winners & b_buy) / len(winners)
        ),
        "bottom_10_loser_avoidance": (
            None if not losers else 1 - len(losers & b_buy) / len(losers)
        ),
        "groups": groups,
        "drawdown_basis": (
            "research cohort curve; not a production position or order curve"
        ),
    }
    if _include_weekly:
        weekly_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in observation_rows:
            iso_year, iso_week, _ = row["trade_date"].isocalendar()
            weekly_rows[f"{iso_year}-W{iso_week:02d}"].append(row)
        result["weekly"] = {}
        for week, rows in sorted(weekly_rows.items()):
            ids = {row["observation_id"] for row in rows}
            result["weekly"][week] = summarize_rows(
                rows,
                [row for row in label_rows if row["observation_id"] in ids],
                _include_weekly=False,
            )
    return result


def load_summary() -> dict[str, Any]:
    with get_connection(read_only=True) as connection:
        observations = [
            {
                "observation_id": row[0],
                "trade_date": row[1],
                "a_score": float(row[2]),
                "b_score": float(row[3]),
                "a_action": row[4],
                "b_action": row[5],
            }
            for row in connection.execute(
                """
                SELECT observation_id, CAST(data_cutoff AS DATE),
                       a_formal_score, b_shadow_formal_score,
                       a_action, b_shadow_action
                FROM overheat_shadow_observations
                ORDER BY data_cutoff, observation_id
                """
            ).fetchall()
        ]
        labels = [
            {
                "observation_id": row[0],
                "horizon": int(row[1]),
                "return": float(row[2]),
                "mfe": float(row[3]),
                "mae": float(row[4]),
            }
            for row in connection.execute(
                """
                SELECT observation_id, horizon_trading_days,
                       forward_return, maximum_favorable_excursion,
                       maximum_adverse_excursion
                FROM overheat_shadow_forward_labels
                """
            ).fetchall()
        ]
        version_row = connection.execute(
            """
            SELECT shadow_version, parameter_hash, effective_at,
                   code_version, frozen
            FROM overheat_shadow_versions
            WHERE frozen = TRUE
            ORDER BY effective_at DESC
            LIMIT 1
            """
        ).fetchone()
    result = summarize_rows(observations, labels)
    result["frozen_shadow"] = None if version_row is None else {
        "shadow_version": version_row[0],
        "parameter_hash": version_row[1],
        "effective_at": version_row[2].isoformat(),
        "code_version": version_row[3],
        "frozen": bool(version_row[4]),
    }
    return result


__all__ = ["load_summary", "summarize_rows"]
